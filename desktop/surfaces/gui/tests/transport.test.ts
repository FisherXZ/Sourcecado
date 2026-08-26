import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { openChat, type SourcecadoSocketEvent } from "../src/api";

class FakeWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  static instances: FakeWebSocket[] = [];

  readyState = FakeWebSocket.CONNECTING;
  readonly sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;

  constructor() {
    FakeWebSocket.instances.push(this);
  }

  send(data: string) {
    this.sent.push(data);
  }

  close() {
    this.readyState = FakeWebSocket.CLOSED;
  }

  open() {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }

  fail(code: number) {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.({ code });
  }

  message(payload: unknown) {
    this.onmessage?.({ data: JSON.stringify(payload) });
  }
}

const waitingItem = {
  id: "queue-1",
  session_id: "thread-alpha",
  text: "Follow up with Alyssa",
  position: 0,
  state: "waiting",
  error: null,
  created_at: "2026-08-26T00:00:00Z",
  updated_at: "2026-08-26T00:00:00Z",
} as const;

const failedItem = {
  ...waitingItem,
  id: "queue-2",
  text: "Draft the intro email",
  position: 1,
  state: "failed",
  error: "provider failed",
} as const;

const snapshot = {
  version: 2,
  type: "queue_snapshot",
  session_id: "thread-alpha",
  command_id: "command-1",
  status: "accepted",
  paused: false,
  items: [waitingItem, failedItem],
} as const;

function queueStates(events: SourcecadoSocketEvent[]): string[][] {
  return events
    .filter((event) => event.type === "queue_snapshot")
    .map((event) =>
      event.type === "queue_snapshot"
        ? event.items.map((item) => item.state)
        : [],
    );
}

describe("openChat reconnect", () => {
  let events: SourcecadoSocketEvent[];

  beforeEach(() => {
    vi.useFakeTimers();
    FakeWebSocket.instances = [];
    vi.stubGlobal("WebSocket", FakeWebSocket);
    events = [];
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("reports the drop, buffers commands, reconnects, and flushes", () => {
    const chat = openChat((event) => events.push(event));
    const first = FakeWebSocket.instances[0]!;
    first.open();

    first.fail(1006);
    expect(events).toContainEqual(
      expect.objectContaining({ type: "connection_change", status: "reconnecting" }),
    );

    chat.send("hello again", "thread-alpha");
    expect(
      events.filter((event) => event.type === "error"),
    ).toEqual([]);
    expect(first.sent).toEqual([]);

    vi.advanceTimersByTime(500);
    const second = FakeWebSocket.instances[1]!;
    expect(second).toBeDefined();
    second.open();

    expect(events).toContainEqual(
      expect.objectContaining({ type: "connection_change", status: "connected" }),
    );
    expect(second.sent).toEqual([
      JSON.stringify({
        type: "chat",
        text: "hello again",
        session_id: "thread-alpha",
      }),
    ]);
  });

  it("surfaces non-1008 close codes instead of staying silent", () => {
    openChat((event) => events.push(event));
    FakeWebSocket.instances[0]!.open();
    events.length = 0;

    FakeWebSocket.instances[0]!.fail(4000);

    expect(events.length).toBeGreaterThan(0);
    expect(events).toContainEqual(
      expect.objectContaining({
        type: "connection_change",
        status: "reconnecting",
        reason: expect.stringContaining("4000"),
      }),
    );
  });

  it("doubles the retry delay on consecutive failures", () => {
    openChat((event) => events.push(event));
    FakeWebSocket.instances[0]!.open();
    FakeWebSocket.instances[0]!.fail(1006);

    vi.advanceTimersByTime(499);
    expect(FakeWebSocket.instances).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(FakeWebSocket.instances).toHaveLength(2);

    FakeWebSocket.instances[1]!.fail(1006);
    vi.advanceTimersByTime(999);
    expect(FakeWebSocket.instances).toHaveLength(2);
    vi.advanceTimersByTime(1);
    expect(FakeWebSocket.instances).toHaveLength(3);
  });

  it("stops after a token rejection instead of retrying a bad token", () => {
    openChat((event) => events.push(event));
    FakeWebSocket.instances[0]!.fail(1008);

    expect(events).toContainEqual(
      expect.objectContaining({
        type: "error",
        message: "sidecar rejected the socket (token)",
      }),
    );
    expect(events).toContainEqual(
      expect.objectContaining({ type: "connection_change", status: "offline" }),
    );
    vi.advanceTimersByTime(60_000);
    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it("does not reconnect after an intentional close", () => {
    const chat = openChat((event) => events.push(event));
    const socket = FakeWebSocket.instances[0]!;
    socket.open();
    events.length = 0;

    chat.close();
    socket.onclose?.({ code: 1000 });

    vi.advanceTimersByTime(60_000);
    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(events).toEqual([]);
  });

  it("fails a command explicitly when the retry buffer is full", () => {
    const chat = openChat((event) => events.push(event));
    FakeWebSocket.instances[0]!.open();
    FakeWebSocket.instances[0]!.fail(1006);

    for (let index = 0; index < 51; index += 1) {
      chat.send(`message ${index}`, "thread-alpha");
    }

    const errors = events.filter((event) => event.type === "error");
    expect(errors).toHaveLength(1);
    expect(errors[0]).toMatchObject({
      message: expect.stringContaining("dropped"),
    });

    vi.advanceTimersByTime(500);
    const second = FakeWebSocket.instances[1]!;
    second.open();
    expect(second.sent).toHaveLength(50);
  });
});

describe("openChat queue visibility during disconnects", () => {
  let events: SourcecadoSocketEvent[];

  beforeEach(() => {
    vi.useFakeTimers();
    FakeWebSocket.instances = [];
    vi.stubGlobal("WebSocket", FakeWebSocket);
    events = [];
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("moves deliverable items to offline, then reconnecting, then back on reconnect", () => {
    openChat((event) => events.push(event));
    const first = FakeWebSocket.instances[0]!;
    first.open();
    first.message(snapshot);
    events.length = 0;

    first.fail(1006);
    expect(queueStates(events)).toEqual([["offline", "failed"]]);

    events.length = 0;
    vi.advanceTimersByTime(500);
    expect(queueStates(events)).toEqual([["reconnecting", "failed"]]);

    events.length = 0;
    FakeWebSocket.instances[1]!.open();
    expect(queueStates(events)).toEqual([["waiting", "failed"]]);
  });
});
