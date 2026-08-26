import { afterEach, describe, expect, it, vi } from "vitest";

import { getSession, openChat, parseChatEvent } from "../src/api";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("parseChatEvent", () => {
  it("accepts the canonical v2 text-delta envelope without changing it", () => {
    const wireEvent = {
      version: 2,
      type: "assistant_delta",
      session_id: "thread-alpha",
      run_id: "run-1",
      event_id: "event-1",
      message_id: "message-1",
      part_id: "part-1",
      delta: "Hello",
    } as const;

    expect(parseChatEvent(wireEvent)).toEqual(wireEvent);
  });

  it("accepts running, complete, failed, stopped, and interrupted text states", () => {
    const envelope = {
      version: 2,
      session_id: "thread-alpha",
      run_id: "run-1",
      event_id: "event-1",
      message_id: "message-1",
      part_id: "part-1",
    } as const;
    const events = [
      { ...envelope, type: "turn_start", state: "running" },
      { ...envelope, type: "turn_end", state: "complete", text: "Done" },
      {
        ...envelope,
        type: "error",
        state: "failed",
        message: "Provider failed",
      },
      {
        ...envelope,
        type: "turn_end",
        state: "stopped",
        text: "Partial",
        message: "Step limit reached",
      },
      {
        ...envelope,
        type: "turn_end",
        state: "interrupted",
        text: "Partial",
      },
    ] as const;

    expect(events.map(parseChatEvent)).toEqual(events);
  });

  it("turns malformed and unknown-version payloads into recoverable notices", () => {
    const malformed = {
      version: 2,
      type: "assistant_delta",
      session_id: "thread-alpha",
      run_id: "run-1",
      event_id: "event-1",
      message_id: "message-1",
      delta: "Hello",
    };
    const unknownVersion = {
      ...malformed,
      version: 99,
      part_id: "part-1",
    };

    expect(parseChatEvent(malformed)).toEqual({
      type: "error",
      message: "Malformed event from sidecar.",
      notice: { code: "malformed_event", recoverable: true },
      session_id: "thread-alpha",
    });
    expect(parseChatEvent(unknownVersion)).toEqual({
      type: "error",
      message: "Unsupported chat event version 99.",
      notice: { code: "unsupported_version", recoverable: true },
      session_id: "thread-alpha",
    });
  });

  it("preserves the existing tool payload fields inside a v2 envelope", () => {
    const envelope = {
      version: 2,
      session_id: "thread-alpha",
      run_id: "run-1",
      event_id: "event-1",
      message_id: "message-1",
      part_id: "call-1",
    } as const;
    const events = [
      {
        ...envelope,
        type: "permission_required",
        id: "call-1",
        name: "gmail_draft",
        arguments: { to: "a@example.com" },
        reason: "Draft creation requires permission.",
      },
      {
        ...envelope,
        type: "tool_started",
        id: "call-1",
        name: "gmail_draft",
        arguments: { to: "a@example.com" },
      },
      {
        ...envelope,
        type: "tool_finished",
        id: "call-1",
        name: "gmail_draft",
        ok: true,
        result: { draft_id: "draft-1" },
      },
    ] as const;

    expect(events.map(parseChatEvent)).toEqual(events);
  });
});

describe("openChat protocol boundary", () => {
  it("parses unknown-version WebSocket payloads before notifying consumers", () => {
    let socket: FakeWebSocket | undefined;
    class FakeWebSocket {
      static readonly OPEN = 1;
      readonly readyState = FakeWebSocket.OPEN;
      onmessage: ((event: MessageEvent) => void) | null = null;
      onerror: (() => void) | null = null;
      onclose: ((event: CloseEvent) => void) | null = null;

      constructor() {
        socket = this;
      }

      send() {}
      close() {}
    }
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const received: unknown[] = [];
    openChat((event) => received.push(event));

    socket?.onmessage?.({
      data: JSON.stringify({
        version: 99,
        type: "assistant_delta",
        session_id: "thread-alpha",
      }),
    } as MessageEvent);

    expect(received).toEqual([
      {
        type: "error",
        message: "Unsupported chat event version 99.",
        notice: { code: "unsupported_version", recoverable: true },
        session_id: "thread-alpha",
      },
    ]);
  });
});

describe("getSession protocol boundary", () => {
  it("returns restored events through the same parser used for live events", async () => {
    const wireEvent = {
      version: 2,
      type: "turn_end",
      session_id: "thread-alpha",
      run_id: "run-1",
      event_id: "event-1",
      message_id: "message-1",
      part_id: "part-1",
      state: "complete",
      text: "Done",
    } as const;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          id: "thread-alpha",
          title: "Research",
          messages: [{ role: "user", content: "Research Codeology" }],
          events: [wireEvent],
        }),
      }),
    );

    await expect(getSession("thread-alpha")).resolves.toEqual({
      id: "thread-alpha",
      title: "Research",
      messages: [{ role: "user", content: "Research Codeology" }],
      events: [wireEvent],
    });
  });
});
