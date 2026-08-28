import { describe, expect, it } from "vitest";

import { parseChatEvent } from "../src/api";
import { convertStructuredMessage } from "../src/chat/messageAdapter";
import { SourcecadoChatStore } from "../src/chat/store";

/**
 * A consequential call was dispatched and never reported back.
 *
 * "Failed" is a claim nobody can make about it, and a director told a send
 * failed is a director who sends it again. The sidecar now ends that turn with
 * `state: "held"`, and the thread has to carry the word through rather than
 * flatten it back into an error.
 */

const envelope = {
  version: 2,
  session_id: "thread-alpha",
  run_id: "run-1",
  event_id: "event-terminal",
  message_id: "message-answer-1",
  part_id: "part-answer-1",
} as const;

const heldEvent = {
  ...envelope,
  type: "error",
  state: "held",
  code: "outcome_unknown",
  effect_id: "effect-9f2c",
  message:
    "The outcome of this action is unknown. It is held for review; do not " +
    "retry it until it is settled.",
} as const;

const runningEvents = [
  { ...envelope, event_id: "event-1", type: "turn_start", state: "running" },
  {
    ...envelope,
    event_id: "event-2",
    type: "assistant_delta",
    delta: "Sending the note.",
  },
];

describe("a turn whose outcome is unknown", () => {
  it("survives the parser instead of arriving as a malformed event", () => {
    expect(parseChatEvent(heldEvent)).toEqual(heldEvent);
  });

  it("still rejects a held terminal that names no effect to settle", () => {
    const { effect_id: _dropped, ...withoutEffect } = heldEvent;
    expect(parseChatEvent(withoutEffect)).toMatchObject({
      notice: { code: "malformed_event", recoverable: true },
    });
  });

  it("marks the message held, not failed", () => {
    const store = new SourcecadoChatStore(
      [{ id: "thread-alpha", messages: [] }],
      "thread-alpha",
    );
    store.replayChatEvents([...runningEvents, heldEvent]);
    expect(store.messagesFor("thread-alpha")[0]?.state).toBe("held");
  });

  it("does not render as an error, because nothing is known to have failed", () => {
    const store = new SourcecadoChatStore(
      [{ id: "thread-alpha", messages: [] }],
      "thread-alpha",
    );
    store.replayChatEvents([...runningEvents, heldEvent]);
    const [message] = store
      .messagesFor("thread-alpha")
      .map(convertStructuredMessage);
    expect(message?.status).not.toMatchObject({ reason: "error" });
    expect(message?.status).toMatchObject({ type: "incomplete" });
  });
});
