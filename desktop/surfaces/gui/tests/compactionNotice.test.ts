import { describe, expect, it } from "vitest";

import { convertStructuredMessage } from "../src/chat/messageAdapter";
import { compactionNoticeText, parseChatEvent } from "../src/chat/protocol";
import { SourcecadoChatStore } from "../src/chat/store";

const turnEnd = (compaction: unknown) =>
  ({
    version: 2,
    type: "turn_end",
    session_id: "thread-alpha",
    run_id: "run-1",
    event_id: "event-end",
    message_id: "message-1",
    part_id: "part-1",
    text: "done",
    state: "complete",
    compaction,
  }) as const;

const notice = {
  generation: 2,
  summarized: true,
  compacted_messages: 42,
  retained_director_messages: 12,
  omitted_director_messages: 3,
  measurement: "provider",
  rejected_summaries: 0,
};

describe("compaction notice parsing", () => {
  it("keeps the counted fields on turn_end", () => {
    const parsed = parseChatEvent(turnEnd(notice));

    expect(parsed.type).toBe("turn_end");
    expect((parsed as { compaction?: unknown }).compaction).toEqual(notice);
  });

  it("drops summary text a sidecar should never have sent", () => {
    const parsed = parseChatEvent(
      turnEnd({ ...notice, summary_text: "the model's account of the session" }),
    );

    const carried = (parsed as { compaction?: Record<string, unknown> }).compaction;
    expect(carried).toBeDefined();
    expect(carried).not.toHaveProperty("summary_text");
    expect(JSON.stringify(parsed)).not.toContain("account of the session");
  });

  it("ignores a malformed compaction field instead of failing the turn", () => {
    const parsed = parseChatEvent(turnEnd("not an object"));

    expect(parsed.type).toBe("turn_end");
    expect((parsed as { compaction?: unknown }).compaction).toBeUndefined();
  });

  it("leaves an ordinary turn_end untouched", () => {
    const event = {
      version: 2,
      type: "turn_end",
      session_id: "thread-alpha",
      run_id: "run-1",
      event_id: "event-end",
      message_id: "message-1",
      part_id: "part-1",
      text: "done",
      state: "complete",
    } as const;

    expect(parseChatEvent(event)).toEqual(event);
  });
});

describe("compaction notice wording", () => {
  it("explains what happened without quoting the summary", () => {
    const text = compactionNoticeText(notice);

    expect(text).toContain("Older parts of this conversation were compacted");
    expect(text).toContain("42");
    expect(text).not.toContain("summary");
  });

  it("says so when no summary could be written", () => {
    const text = compactionNoticeText({ ...notice, summarized: false });

    expect(text).toContain("no summary of them was available");
  });
});

describe("compaction notice in the thread", () => {
  it("adds one readable notice message the operator can see", () => {
    const store = new SourcecadoChatStore([], "thread-alpha");
    store.applyChatEvent({
      version: 2,
      type: "turn_start",
      session_id: "thread-alpha",
      run_id: "run-1",
      event_id: "event-start",
      message_id: "message-1",
      part_id: "part-1",
      state: "running",
    });
    store.applyChatEvent(turnEnd(notice));

    const messages = store.messagesFor("thread-alpha");
    const notices = messages.flatMap((message) =>
      message.parts.filter((part) => part.type === "notice"),
    );

    expect(notices).toHaveLength(1);
    expect(notices[0]).toMatchObject({ code: "compacted" });
    const adapted = messages.map(convertStructuredMessage);
    const rendered = JSON.stringify(adapted);
    expect(rendered).toContain("Older parts of this conversation were compacted");
  });

  it("adds no notice when the turn compacted nothing", () => {
    const store = new SourcecadoChatStore([], "thread-alpha");
    store.applyChatEvent({
      version: 2,
      type: "turn_start",
      session_id: "thread-beta",
      run_id: "run-2",
      event_id: "event-start-2",
      message_id: "message-2",
      part_id: "part-2",
      state: "running",
    });
    store.applyChatEvent({
      version: 2,
      type: "turn_end",
      session_id: "thread-beta",
      run_id: "run-2",
      event_id: "event-end-2",
      message_id: "message-2",
      part_id: "part-2",
      text: "done",
      state: "complete",
    });

    const notices = store
      .messagesFor("thread-beta")
      .flatMap((message) => message.parts.filter((part) => part.type === "notice"));

    expect(notices).toHaveLength(0);
  });

  it("does not repeat the notice when the same event is replayed", () => {
    const store = new SourcecadoChatStore([], "thread-alpha");
    store.applyChatEvent(turnEnd(notice));
    store.applyChatEvent(turnEnd(notice));

    const notices = store
      .messagesFor("thread-alpha")
      .flatMap((message) => message.parts.filter((part) => part.type === "notice"));

    expect(notices).toHaveLength(1);
  });
});
