import type { AppendMessage } from "@assistant-ui/react";
import { describe, expect, it, vi } from "vitest";

import { createPersistedQueueAdapter } from "../src/chat/persistedQueue";

function message(text: string): AppendMessage {
  return {
    role: "user",
    content: [{ type: "text", text }],
    attachments: [],
    metadata: { custom: {} },
    createdAt: new Date(),
    parentId: null,
    sourceId: null,
    runConfig: undefined,
  } as unknown as AppendMessage;
}

function adapter(running: boolean) {
  const send = vi.fn();
  const dispatch = vi.fn();
  const queue = createPersistedQueueAdapter({
    sessionId: "thread-alpha",
    items: [],
    running,
    send,
    dispatch,
  });
  return { queue, send, dispatch };
}

describe("persisted queue adapter, send and send-and-steer", () => {
  it("starts a steered message at once when no run is active", () => {
    const { queue, send, dispatch } = adapter(false);
    const steered = message("Find the founders");

    queue.steer(steered);

    expect(dispatch).toHaveBeenCalledOnce();
    expect(dispatch).toHaveBeenCalledWith(steered);
    expect(send).not.toHaveBeenCalled();
  });

  it("queues a steered message while a run is active", () => {
    const { queue, send, dispatch } = adapter(true);

    queue.steer(message("Find the founders"));

    expect(dispatch).not.toHaveBeenCalled();
    expect(send).toHaveBeenCalledOnce();
    expect(send.mock.calls[0]?.[0]).toMatchObject({
      type: "queue_add",
      session_id: "thread-alpha",
      text: "Find the founders",
    });
  });

  it("queues nothing for an empty steered message during a run", () => {
    const { queue, send, dispatch } = adapter(true);

    queue.steer(message("   "));

    expect(send).not.toHaveBeenCalled();
    expect(dispatch).not.toHaveBeenCalled();
  });

  it("treats a plain send the same way: at once when idle, queued during a run", () => {
    const idle = adapter(false);
    idle.queue.enqueue(message("Plain send"));
    expect(idle.dispatch).toHaveBeenCalledOnce();
    expect(idle.send).not.toHaveBeenCalled();

    const busy = adapter(true);
    busy.queue.enqueue(message("Plain send"));
    expect(busy.dispatch).not.toHaveBeenCalled();
    expect(busy.send.mock.calls[0]?.[0]).toMatchObject({ type: "queue_add", text: "Plain send" });
  });
});
