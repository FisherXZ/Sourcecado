import { describe, expect, it } from "vitest";

import { convertStructuredMessage } from "../src/chat/messageAdapter";
import { parseChatEvent } from "../src/chat/protocol";
import { restoreConversation } from "../src/chat/restoreConversation";
import { SourcecadoChatStore } from "../src/chat/store";
import {
  joinReasoning,
  reasoningParts,
  reasoningTiming,
  thinkingElapsedMs,
  thoughtLabel,
} from "../src/chat/thinking";

const envelope = {
  version: 2,
  session_id: "thread-alpha",
  run_id: "run-1",
  message_id: "message-1",
  part_id: "part-1",
} as const;

const T0 = Date.parse("2026-10-26T12:00:00.000Z");
const at = (seconds: number) => new Date(T0 + seconds * 1000).toISOString();

let counter = 0;
const event = (fields: Record<string, unknown>) => ({
  ...envelope,
  event_id: `event-${++counter}`,
  ...fields,
});

const newStore = () =>
  new SourcecadoChatStore([{ id: "thread-alpha", messages: [] }], "thread-alpha");

const parts = (store: SourcecadoChatStore) =>
  store.messagesFor("thread-alpha")[0]!.parts;

describe("reasoning_delta protocol", () => {
  it("accepts the v2 envelope and keeps step and time", () => {
    const parsed = parseChatEvent(
      event({ type: "reasoning_delta", delta: "Checking.", step: 2, at: at(1) }),
    );

    expect(parsed).toMatchObject({
      type: "reasoning_delta",
      delta: "Checking.",
      step: 2,
      at: at(1),
    });
  });

  it("accepts a legacy-shaped event with no step or time", () => {
    const parsed = parseChatEvent(event({ type: "reasoning_delta", delta: "Checking." }));

    expect(parsed).toMatchObject({ type: "reasoning_delta", delta: "Checking." });
  });

  it("turns a reasoning event without text into a recoverable notice", () => {
    const parsed = parseChatEvent(event({ type: "reasoning_delta", delta: 7 }));

    expect(parsed).toMatchObject({ type: "error", notice: { code: "malformed_event" } });
  });
});

describe("reasoning in the structured thread", () => {
  it("collects reasoning into one running part and settles it when the answer starts", () => {
    const store = newStore();
    store.applyChatEvent(event({ type: "turn_start", state: "running" }));
    store.applyChatEvent(
      event({ type: "reasoning_delta", step: 1, at: at(0), delta: "First " }),
    );
    store.applyChatEvent(
      event({ type: "reasoning_delta", step: 1, at: at(3), delta: "thought." }),
    );

    expect(parts(store)).toEqual([
      {
        type: "reasoning",
        id: "part-1:reasoning:1",
        text: "First thought.",
        state: "running",
        startedAt: T0,
        endedAt: T0 + 3000,
      },
    ]);

    store.applyChatEvent(event({ type: "assistant_delta", delta: "Answer." }));

    expect(parts(store).map((part) => [part.type, "state" in part ? part.state : null])).toEqual([
      ["reasoning", "complete"],
      ["text", "running"],
    ]);
  });

  it("settles reasoning when a tool call starts and opens a new block for the next step", () => {
    const store = newStore();
    store.applyChatEvent(event({ type: "turn_start", state: "running" }));
    store.applyChatEvent(
      event({ type: "reasoning_delta", step: 1, at: at(0), delta: "Plan." }),
    );
    store.applyChatEvent(
      event({ type: "tool_started", id: "call-1", name: "now", arguments: {} }),
    );
    store.applyChatEvent(
      event({ type: "reasoning_delta", step: 2, at: at(5), delta: "Read." }),
    );

    expect(parts(store).map((part) => [part.type, part.id])).toEqual([
      ["reasoning", "part-1:reasoning:1"],
      ["tool", "call-1"],
      ["reasoning", "part-1:reasoning:2"],
    ]);
    const states = parts(store).flatMap((part) =>
      part.type === "reasoning" ? [part.state] : [],
    );
    expect(states).toEqual(["complete", "running"]);
  });

  it("settles reasoning when the turn ends with no answer", () => {
    const store = newStore();
    store.applyChatEvent(event({ type: "turn_start", state: "running" }));
    store.applyChatEvent(
      event({ type: "reasoning_delta", step: 1, at: at(0), delta: "Thinking." }),
    );
    store.applyChatEvent(event({ type: "turn_end", state: "complete", text: "" }));

    expect(parts(store)[0]).toMatchObject({ type: "reasoning", state: "complete" });
  });

  it("applies one reasoning event once and replays to the same thread as the live stream", () => {
    const turn = [
      event({ type: "turn_start", state: "running" }),
      event({ type: "reasoning_delta", step: 1, at: at(0), delta: "A " }),
      event({ type: "reasoning_delta", step: 1, at: at(2), delta: "B." }),
      event({ type: "assistant_delta", delta: "Answer." }),
      event({ type: "turn_end", state: "complete", text: "Answer." }),
    ];
    const live = newStore();
    for (const item of turn) live.applyChatEvent(item);
    const replayed = newStore();
    replayed.replayChatEvents([...turn, turn[1], turn[2]]);

    expect(replayed.messagesFor("thread-alpha")).toEqual(
      live.messagesFor("thread-alpha"),
    );
    expect(parts(replayed)[0]).toMatchObject({ text: "A B." });
  });

  it("projects a reasoning part with its times for the thread, and none when there was none", () => {
    const store = newStore();
    store.replayChatEvents([
      event({ type: "turn_start", state: "running" }),
      event({ type: "reasoning_delta", step: 1, at: at(0), delta: "Plan the search." }),
      event({ type: "reasoning_delta", step: 1, at: at(12), delta: " Go." }),
      event({ type: "assistant_delta", delta: "Answer." }),
      event({ type: "turn_end", state: "complete", text: "Answer." }),
    ]);

    const message = convertStructuredMessage(store.messagesFor("thread-alpha")[0]!);
    const found = reasoningParts(message.content as never);

    expect(found).toHaveLength(1);
    expect(joinReasoning(found)).toBe("Plan the search. Go.");
    const timing = reasoningTiming(found);
    expect(timing).toEqual({ settledMs: 12_000, runningSince: null, known: true });
    expect(thoughtLabel(thinkingElapsedMs(timing, T0 + 99_000))).toBe("Thought for 12s");

    const plain = convertStructuredMessage({
      id: "m",
      role: "assistant",
      state: "complete",
      parts: [{ type: "text", id: "t", text: "Hi", state: "complete" }],
    });
    expect(reasoningParts(plain.content as never)).toEqual([]);
  });

  it("sums finished blocks and counts a running block up from its start", () => {
    const timing = reasoningTiming([
      { type: "reasoning", text: "a", status: { type: "complete" }, providerMetadata: { sourcecado: { startedAt: T0, endedAt: T0 + 4000 } } },
      { type: "reasoning", text: "b", status: { type: "running" }, providerMetadata: { sourcecado: { startedAt: T0 + 10_000, endedAt: T0 + 10_500 } } },
    ]);

    expect(thinkingElapsedMs(timing, T0 + 13_000)).toBe(7000);
  });

  it("says Thought, with no seconds, when no time was recorded", () => {
    const timing = reasoningTiming([
      { type: "reasoning", text: "a", status: { type: "complete" } },
    ]);

    expect(thinkingElapsedMs(timing, T0)).toBeNull();
    expect(thoughtLabel(null)).toBe("Thought");
  });
});

describe("restoring reasoning from the conversation transcript", () => {
  it("keeps the reasoning part beside the text that replaces the legacy assistant record", () => {
    const restored = restoreConversation({
      id: "thread-alpha",
      title: "Recruiting",
      messages: [
        { role: "user", content: "Find leads." },
        { role: "assistant", content: "Three leads.", message_id: "message-1" },
      ],
      events: [
        event({ type: "turn_start", state: "running" }),
        event({ type: "reasoning_delta", step: 1, at: at(0), delta: "Plan." }),
        event({ type: "assistant_delta", delta: "Three leads." }),
        event({ type: "turn_end", state: "complete", text: "Three leads." }),
      ],
    });

    const assistant = restored.find((message) => message.role === "assistant")!;
    expect(assistant.parts.map((part) => part.type)).toEqual(["reasoning", "text"]);
    expect(restored).toHaveLength(2);
  });

  it("restores a history written before reasoning events with no reasoning part", () => {
    const restored = restoreConversation({
      id: "thread-alpha",
      title: "Recruiting",
      messages: [
        { role: "user", content: "Find leads." },
        { role: "assistant", content: "Three leads." },
      ],
      events: [
        event({ type: "turn_start", state: "running" }),
        event({ type: "assistant_delta", delta: "Three leads." }),
        event({ type: "turn_end", state: "complete", text: "Three leads." }),
      ],
    });

    expect(
      restored.flatMap((message) => message.parts).some((part) => part.type === "reasoning"),
    ).toBe(false);
  });
});
