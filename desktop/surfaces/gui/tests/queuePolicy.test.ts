import type { AppendMessage } from "@assistant-ui/react";
import { describe, expect, it, vi } from "vitest";

import { SourcecadoQueuePolicy } from "../src/chat/queuePolicy";

const queuedMessage = (text: string): AppendMessage => ({
  role: "user",
  content: [{ type: "text", text }],
  attachments: [],
  createdAt: new Date(0),
  metadata: { custom: {} },
  parentId: null,
  sourceId: null,
  runConfig: undefined,
});

describe("SourcecadoQueuePolicy", () => {
  it("keeps queued work paused when cancellation settles", () => {
    const dispatch = vi.fn();
    const cancelTransport = vi.fn();
    const policy = new SourcecadoQueuePolicy({ dispatch, cancelTransport });
    policy.beginRun();
    policy.enqueue(queuedMessage("first queued correction"));
    policy.enqueue(queuedMessage("second queued correction"));

    policy.cancelRun();
    policy.settleRun();

    expect(cancelTransport).toHaveBeenCalledOnce();
    expect(dispatch).not.toHaveBeenCalled();
    expect(policy.pendingPrompts()).toEqual([
      "first queued correction",
      "second queued correction",
    ]);
  });

  it("dispatches only the oldest item after an explicit resume", () => {
    const dispatch = vi.fn();
    const policy = new SourcecadoQueuePolicy({
      dispatch,
      cancelTransport: vi.fn(),
    });
    policy.beginRun();
    policy.enqueue(queuedMessage("first queued correction"));
    policy.enqueue(queuedMessage("second queued correction"));
    policy.cancelRun();
    policy.settleRun();

    policy.resumeQueuedRun();

    expect(dispatch).toHaveBeenCalledOnce();
    expect(dispatch.mock.calls[0]?.[0].content).toEqual([
      { type: "text", text: "first queued correction" },
    ]);
    expect(policy.pendingPrompts()).toEqual(["second queued correction"]);
  });
});
