import type { QueueItem } from "../api";
import { useState } from "react";

import { ContextChips } from "./ContextChips";
import {
  ArrowDownIcon,
  ArrowUpIcon,
  CheckIcon,
  EditIcon,
  PlayIcon,
  RetryIcon,
  TrashIcon,
} from "./icons";
import { splitContext, withContext } from "./messageContext";

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
      <p className="sourcecado-queue-title">
        Queued messages
        <span className="sourcecado-queue-count">{items.length}</span>
      </p>
      {paused ? (
        <button type="button" onClick={onResume}>
          <PlayIcon />
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
  // A queued message may carry attached context. The row shows the text and
  // the chips apart, and an edit changes the text only.
  const { body, chips } = splitContext(item.text);
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(body);
  const mutable = item.state !== "sending";
  return (
    <li data-queue-item-id={item.id}>
      {editing ? (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (!text.trim()) return;
            onEdit(item.id, withContext(text.trim(), chips));
            setEditing(false);
          }}
        >
          <input
            aria-label="Edit queued message"
            value={text}
            onChange={(event) => setText(event.currentTarget.value)}
          />
          <button type="submit" aria-label="Save queued message" title="Save">
            <CheckIcon />
          </button>
        </form>
      ) : (
        <div className="sourcecado-queue-text">
          <span>{body}</span>
          <ContextChips chips={chips} />
        </div>
      )}
      <strong>{queueStateLabel(item.state)}</strong>
      <div className="sourcecado-queue-actions">
        <span className="sourcecado-queue-action-slot">
          {previousId && mutable ? (
            <button
              type="button"
              aria-label={`Move ${body} up`}
              title="Move up"
              onClick={() => onMove(item.id, previousId)}
            >
              <ArrowUpIcon />
            </button>
          ) : null}
        </span>
        <span className="sourcecado-queue-action-slot">
          {nextId && mutable ? (
            <button
              type="button"
              aria-label={`Move ${body} down`}
              title="Move down"
              onClick={() => onMove(item.id, undefined, nextId)}
            >
              <ArrowDownIcon />
            </button>
          ) : null}
        </span>
        <span className="sourcecado-queue-action-slot">
          {mutable ? (
            <button
              type="button"
              aria-label={`Edit ${body}`}
              title="Edit"
              onClick={() => setEditing(true)}
            >
              <EditIcon />
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
              title="Retry"
              onClick={() => onRetry(item.id)}
            >
              <RetryIcon />
            </button>
          ) : null}
        </span>
        <span className="sourcecado-queue-action-slot">
          {mutable ? (
            <button
              type="button"
              aria-label={`Remove ${body}`}
              title="Remove"
              onClick={() => onRemove(item.id)}
            >
              <TrashIcon />
            </button>
          ) : null}
        </span>
      </div>
    </li>
  );
}
