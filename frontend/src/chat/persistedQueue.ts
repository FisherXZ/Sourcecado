import type {
  AppendMessage,
  ExternalThreadQueueAdapter,
} from "@assistant-ui/react";

import type { QueueCommand, SourcecadoQueueItem } from "./protocol";

let fallbackId = 0;

function newId(prefix: string): string {
  const random = globalThis.crypto?.randomUUID?.();
  return random ? `${prefix}_${random}` : `${prefix}_${Date.now()}_${++fallbackId}`;
}

export function createQueueCommandId(): string {
  return newId("command");
}

function textOf(message: AppendMessage): string {
  return message.content
    .filter((part) => part.type === "text")
    .map((part) => (part.type === "text" ? part.text : ""))
    .join("")
    .trim();
}

type PersistedQueueAdapterOptions = {
  readonly sessionId: string;
  readonly items: readonly SourcecadoQueueItem[];
  readonly running: boolean;
  readonly send: (command: QueueCommand) => void;
  readonly dispatch: (message: AppendMessage) => Promise<void> | void;
};

type QueuePlacement = Parameters<ExternalThreadQueueAdapter["move"]>[1];

export function createPersistedQueueAdapter({
  sessionId,
  items,
  running,
  send,
  dispatch,
}: PersistedQueueAdapterOptions): ExternalThreadQueueAdapter {
  const command = (
    value:
      | Omit<Extract<QueueCommand, { type: "queue_add" }>, "session_id" | "command_id">
      | Omit<Extract<QueueCommand, { type: "queue_edit" }>, "session_id" | "command_id">
      | Omit<Extract<QueueCommand, { type: "queue_move" }>, "session_id" | "command_id">
      | Omit<Extract<QueueCommand, { type: "queue_remove" | "queue_retry" }>, "session_id" | "command_id">,
  ) =>
    send({
      ...value,
      session_id: sessionId,
      command_id: createQueueCommandId(),
    } as QueueCommand);

  const enqueue = (message: AppendMessage) => {
    if (!running) {
      void dispatch(message);
      return;
    }
    const text = textOf(message);
    if (!text) return;
    command({ type: "queue_add", item_id: newId("queue"), text });
  };

  return {
    items: items.map((item) => ({
      id: item.id,
      prompt: item.text,
      parts: [{ type: "text" as const, text: item.text }],
    })),
    steerItems: [],
    enqueue,
    steer(message: AppendMessage) {
      const text = textOf(message);
      if (!text) return;
      command({ type: "queue_add", item_id: newId("queue"), text });
    },
    move(queueItemId: string, placement: QueuePlacement) {
      command({
        type: "queue_move",
        item_id: queueItemId,
        ...(placement.insertBefore
          ? { before_id: placement.insertBefore }
          : {}),
        ...(placement.insertAfter ? { after_id: placement.insertAfter } : {}),
      });
    },
    edit(queueItemId: string, message: AppendMessage) {
      const text = textOf(message);
      if (!text) return;
      command({ type: "queue_edit", item_id: queueItemId, text });
    },
    remove(queueItemId: string) {
      command({ type: "queue_remove", item_id: queueItemId });
    },
    __internal_notifyCancelled() {
      // The cancel command pauses the authoritative backend queue.
    },
  };
}
