import type { QueueItem } from "../api";
import { useState } from "react";

function queueStateLabel(state: QueueItem["state"]): string {
  switch (state) {
    case "waiting":
      return "Waiting";
    case "sending":
      return "Sending";
    case "failed":
      return "Failed";
    case "interrupted":
      return "Interrupted";
    case "offline":
      return "Offline";
    case "reconnecting":
      return "Reconnecting";
  }
}

export function Queue({
  items,
  paused,
  onRetry,
  onResume,
  onMove,
  onEdit,
  onRemove,
}: {
  readonly items: readonly QueueItem[];
  readonly paused: boolean;
  readonly onRetry: (itemId: string) => void;
  readonly onResume: () => void;
  readonly onMove: (itemId: string, beforeId?: string, afterId?: string) => void;
  readonly onEdit: (itemId: string, text: string) => void;
  readonly onRemove: (itemId: string) => void;
}) {
  if (items.length === 0) return null;
  return (
    <section className="sourcecado-queue" role="region" aria-label="Queued messages">
      <p className="eyebrow">Queued messages</p>
      {paused ? (
        <button type="button" onClick={onResume}>
          Resume queue
        </button>
      ) : null}
      <ol>
        {items.map((item, index) => (
          <QueueRow
            key={item.id}
            item={item}
            previousId={items[index - 1]?.id}
            nextId={items[index + 1]?.id}
            onRetry={onRetry}
            onMove={onMove}
            onEdit={onEdit}
            onRemove={onRemove}
          />
        ))}
      </ol>
    </section>
  );
}

function QueueRow({
  item,
  previousId,
  nextId,
  onRetry,
  onMove,
  onEdit,
  onRemove,
}: {
  readonly item: QueueItem;
  readonly previousId?: string;
  readonly nextId?: string;
  readonly onRetry: (itemId: string) => void;
  readonly onMove: (itemId: string, beforeId?: string, afterId?: string) => void;
  readonly onEdit: (itemId: string, text: string) => void;
  readonly onRemove: (itemId: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(item.text);
  const mutable = item.state !== "sending";
  return (
    <li data-queue-item-id={item.id}>
      {editing ? (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (!text.trim()) return;
            onEdit(item.id, text.trim());
            setEditing(false);
          }}
        >
          <input
            aria-label="Edit queued message"
            value={text}
            onChange={(event) => setText(event.currentTarget.value)}
          />
          <button type="submit" aria-label="Save queued message">
            Save
          </button>
        </form>
      ) : (
        <span>{item.text}</span>
      )}
      <strong>{queueStateLabel(item.state)}</strong>
      <div className="sourcecado-queue-actions">
        <span className="sourcecado-queue-action-slot">
          {previousId && mutable ? (
            <button
              type="button"
              aria-label={`Move ${item.text} up`}
              onClick={() => onMove(item.id, previousId)}
            >
              Up
            </button>
          ) : null}
        </span>
        <span className="sourcecado-queue-action-slot">
          {nextId && mutable ? (
            <button
              type="button"
              aria-label={`Move ${item.text} down`}
              onClick={() => onMove(item.id, undefined, nextId)}
            >
              Down
            </button>
          ) : null}
        </span>
        <span className="sourcecado-queue-action-slot">
          {mutable ? (
            <button
              type="button"
              aria-label={`Edit ${item.text}`}
              onClick={() => setEditing(true)}
            >
              Edit
            </button>
          ) : null}
        </span>
        <span className="sourcecado-queue-action-slot">
          {item.state === "failed" ||
          item.state === "interrupted" ||
          item.state === "offline" ? (
            <button
              type="button"
              aria-label="Retry queued message"
              onClick={() => onRetry(item.id)}
            >
              Retry
            </button>
          ) : null}
        </span>
        <span className="sourcecado-queue-action-slot">
          {mutable ? (
            <button
              type="button"
              aria-label={`Remove ${item.text}`}
              onClick={() => onRemove(item.id)}
            >
              Remove
            </button>
          ) : null}
        </span>
      </div>
    </li>
  );
}
