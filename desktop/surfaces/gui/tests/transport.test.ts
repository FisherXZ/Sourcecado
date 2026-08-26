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

const envelopeEvent = (n: number, extra: Record<string, unknown>) => ({
  version: 2,
  session_id: "thread-alpha",
  run_id: "run-1",
  event_id: `evt-${n}`,
  message_id: "message-1",
  part_id: "part-1",
  ...extra,
});

const conversationBody = (events: readonly unknown[]) => ({
  id: "thread-alpha",
  title: null,
  messages: [],
  events,
  queue: [],
  queue_paused: false,
});

async function flushAsync() {
  for (let index = 0; index < 10; index += 1) {
    await Promise.resolve();
  }
}

function deliveredIds(events: SourcecadoSocketEvent[]): string[] {
  return events
    .filter(
      (event): event is SourcecadoSocketEvent & { event_id: string } =>
        "event_id" in event && typeof event.event_id === "string",
    )
    .map((event) => event.event_id);
}

describe("openChat reconnect re-sync", () => {
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

  it("replays the events a run emitted while the socket was down, including its terminal", async () => {
    const turnStart = envelopeEvent(1, { type: "turn_start", state: "running" });
    const liveDelta = envelopeEvent(2, {
      type: "assistant_delta",
      delta: "Working",
    });
    const missedDelta = envelopeEvent(3, {
      type: "assistant_delta",
      delta: " on it. Done.",
    });
    const missedEnd = envelopeEvent(4, {
      type: "turn_end",
      text: "Working on it. Done.",
      state: "complete",
    });
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () =>
        conversationBody([turnStart, liveDelta, missedDelta, missedEnd]),
    });
    vi.stubGlobal("fetch", fetchMock);

    openChat((event) => events.push(event));
    const first = FakeWebSocket.instances[0]!;
    first.open();
    first.message(turnStart);
    first.message(liveDelta);

    first.fail(1006);
    await vi.advanceTimersByTimeAsync(500);
    FakeWebSocket.instances[1]!.open();
    await flushAsync();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain(
      "/v1/sessions/thread-alpha",
    );
    const ids = deliveredIds(events);
    // The missed tail arrives exactly once; nothing already delivered repeats.
    expect(ids.filter((id) => id === "evt-3")).toHaveLength(1);
    expect(ids.filter((id) => id === "evt-4")).toHaveLength(1);
    expect(ids.filter((id) => id === "evt-1")).toHaveLength(1);
    expect(ids.filter((id) => id === "evt-2")).toHaveLength(1);
  });

  it("does not refetch a session whose runs all ended before the drop", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    openChat((event) => events.push(event));
    const first = FakeWebSocket.instances[0]!;
    first.open();
    first.message(envelopeEvent(1, { type: "turn_start", state: "running" }));
    first.message(
      envelopeEvent(2, { type: "turn_end", text: "Done.", state: "complete" }),
    );

    first.fail(1006);
    await vi.advanceTimersByTimeAsync(500);
    FakeWebSocket.instances[1]!.open();
    await flushAsync();

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("holds live socket events during the re-sync and flushes them after the replayed tail", async () => {
    const turnStart = envelopeEvent(1, { type: "turn_start", state: "running" });
    const missedDelta = envelopeEvent(3, {
      type: "assistant_delta",
      delta: "missed",
    });
    let resolveFetch: (value: unknown) => void = () => {};
    const fetchMock = vi.fn().mockReturnValue(
      new Promise((resolve) => {
        resolveFetch = resolve;
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    openChat((event) => events.push(event));
    const first = FakeWebSocket.instances[0]!;
    first.open();
    first.message(turnStart);

    first.fail(1006);
    await vi.advanceTimersByTimeAsync(500);
    const second = FakeWebSocket.instances[1]!;
    second.open();
    await flushAsync();

    second.message(envelopeEvent(5, { type: "assistant_delta", delta: "live" }));
    resolveFetch({
      ok: true,
      json: async () => conversationBody([turnStart, missedDelta]),
    });
    await flushAsync();

    const ids = deliveredIds(events);
    expect(ids.indexOf("evt-3")).toBeGreaterThan(-1);
    expect(ids.indexOf("evt-5")).toBeGreaterThan(ids.indexOf("evt-3"));
  });

  it("reports a failed re-sync as a transport failure and still delivers held events", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new Error("boom"));
    vi.stubGlobal("fetch", fetchMock);

    openChat((event) => events.push(event));
    const first = FakeWebSocket.instances[0]!;
    first.open();
    first.message(envelopeEvent(1, { type: "turn_start", state: "running" }));

    first.fail(1006);
    await vi.advanceTimersByTimeAsync(500);
    const second = FakeWebSocket.instances[1]!;
    second.open();
    second.message(envelopeEvent(5, { type: "assistant_delta", delta: "live" }));
    await flushAsync();

    expect(events).toContainEqual(
      expect.objectContaining({
        type: "error",
        message: expect.stringContaining("re-sync"),
        session_id: "thread-alpha",
      }),
    );
    expect(deliveredIds(events)).toContain("evt-5");
  });

  it("re-syncs a session whose send was delivered but never answered with turn_start", async () => {
    const turnStart = envelopeEvent(1, { type: "turn_start", state: "running" });
    const turnEnd = envelopeEvent(2, {
      type: "turn_end",
      text: "Done.",
      state: "complete",
    });
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => conversationBody([turnStart, turnEnd]),
    });
    vi.stubGlobal("fetch", fetchMock);

    const chat = openChat((event) => events.push(event));
    const first = FakeWebSocket.instances[0]!;
    first.open();
    expect(chat.send("find leads", "thread-alpha")).toEqual({
      state: "delivered",
    });

    first.fail(1006);
    await vi.advanceTimersByTimeAsync(500);
    FakeWebSocket.instances[1]!.open();
    await flushAsync();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(deliveredIds(events)).toEqual(["evt-1", "evt-2"]);

    // The replayed terminal settles the session: the next drop needs no fetch.
    FakeWebSocket.instances[1]!.fail(1006);
    await vi.advanceTimersByTimeAsync(1000);
    FakeWebSocket.instances[2]!.open();
    await flushAsync();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("openChat command delivery signal", () => {
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

  it("reports delivered for a command handed to an open socket", () => {
    const chat = openChat((event) => events.push(event));
    const socket = FakeWebSocket.instances[0]!;
    socket.open();

    expect(chat.approve("approval-1", "allow")).toEqual({ state: "delivered" });
    expect(socket.sent).toEqual([
      JSON.stringify({ type: "permission", id: "approval-1", decision: "allow" }),
    ]);
  });

  it("reports queued for a command buffered while the socket is down", () => {
    const chat = openChat((event) => events.push(event));
    const socket = FakeWebSocket.instances[0]!;
    socket.open();
    socket.fail(1006);

    expect(chat.approve("approval-1", "deny")).toEqual({ state: "queued" });

    vi.advanceTimersByTime(500);
    const second = FakeWebSocket.instances[1]!;
    second.open();
    expect(second.sent).toEqual([
      JSON.stringify({ type: "permission", id: "approval-1", decision: "deny" }),
    ]);
  });

  it("reports dropped when the retry buffer is full", () => {
    const chat = openChat((event) => events.push(event));
    FakeWebSocket.instances[0]!.open();
    FakeWebSocket.instances[0]!.fail(1006);

    for (let index = 0; index < 50; index += 1) {
      expect(chat.send(`message ${index}`, "thread-alpha")).toEqual({
        state: "queued",
      });
    }
    expect(chat.approve("approval-1", "allow")).toEqual({
      state: "dropped",
      reason: expect.stringContaining("dropped") as unknown as string,
    });
  });

  it("reports dropped instead of buffering forever after a token rejection", () => {
    const chat = openChat((event) => events.push(event));
    FakeWebSocket.instances[0]!.fail(1008);
    events.length = 0;

    const delivery = chat.approve("approval-1", "allow");
    expect(delivery.state).toBe("dropped");
    expect(events).toContainEqual(
      expect.objectContaining({
        type: "error",
        message: expect.stringContaining("dropped"),
      }),
    );
    vi.advanceTimersByTime(60_000);
    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it("surfaces commands stranded in the buffer when the connection dies terminally", () => {
    const chat = openChat((event) => events.push(event));
    const first = FakeWebSocket.instances[0]!;
    first.open();
    first.fail(1006);

    expect(chat.approve("approval-1", "allow")).toEqual({ state: "queued" });
    events.length = 0;

    vi.advanceTimersByTime(500);
    FakeWebSocket.instances[1]!.fail(1008);

    expect(events).toContainEqual(
      expect.objectContaining({
        type: "error",
        message: expect.stringContaining("1 queued command"),
      }),
    );

    vi.advanceTimersByTime(60_000);
    expect(FakeWebSocket.instances).toHaveLength(2);
  });

  it("reports dropped for a command issued after the transport is closed", () => {
    const chat = openChat((event) => events.push(event));
    FakeWebSocket.instances[0]!.open();
    chat.close();

    expect(chat.approve("approval-1", "allow").state).toBe("dropped");
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
