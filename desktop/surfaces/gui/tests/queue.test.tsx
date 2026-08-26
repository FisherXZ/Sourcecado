import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Queue } from "../src/chat/Queue";
import type { QueueItem } from "../src/api";

function item(overrides: Partial<QueueItem> = {}): QueueItem {
  return {
    id: "item-1",
    session_id: "thread-1",
    text: "Draft outreach",
    position: 0,
    state: "waiting",
    error: null,
    created_at: "2026-08-25T12:00:00Z",
    updated_at: "2026-08-25T12:00:00Z",
    ...overrides,
  };
}

const noop = () => {};

describe("Queue", () => {
  it.each([
    "waiting",
    "sending",
    "failed",
    "interrupted",
    "offline",
    "reconnecting",
  ] as const)(
    "reserves the same five action slots for a %s item so columns do not jump",
    (state) => {
      const { container } = render(
        <Queue
          items={[item({ state })]}
          paused={false}
          onRetry={noop}
          onResume={noop}
          onMove={noop}
          onEdit={noop}
          onRemove={noop}
        />,
      );
      const actions = container.querySelector(".sourcecado-queue-actions");
      expect(
        actions?.querySelectorAll(".sourcecado-queue-action-slot"),
      ).toHaveLength(5);
    },
  );

  it("keeps action columns aligned across rows regardless of position or state", () => {
    const { container } = render(
      <Queue
        items={[
          item({ id: "item-first", position: 0, state: "failed" }),
          item({ id: "item-second", position: 1, state: "waiting" }),
        ]}
        paused={false}
        onRetry={noop}
        onResume={noop}
        onMove={noop}
        onEdit={noop}
        onRemove={noop}
      />,
    );
    const rows = container.querySelectorAll(".sourcecado-queue-actions");
    expect(rows).toHaveLength(2);
    for (const row of rows) {
      expect(row.querySelectorAll(".sourcecado-queue-action-slot")).toHaveLength(5);
    }
  });
});
