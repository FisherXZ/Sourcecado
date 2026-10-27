import { act, render, screen } from "@testing-library/react";
import type { ToolCallMessagePart } from "@assistant-ui/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { parseChatEvent } from "../src/api";
import { ActivityGroup } from "../src/chat/ActivityGroup";
import { convertStructuredMessage } from "../src/chat/messageAdapter";
import { SourcecadoChatStore } from "../src/chat/store";

/**
 * Frame order of a denied approval, as the backend sends it (turn.py): the
 * denied call is closed with tool_finished ok=false and a failure record, and
 * the approval receipt (approval_resolved) follows. In the real app the two land
 * a few milliseconds apart. Here they land 1.5 seconds apart, so every state in
 * between is on screen long enough to read.
 *
 * The step row must never say Failed for a denial, in the gap or after it.
 */
const envelope = {
  version: 2,
  session_id: "t",
  run_id: "run-1",
  message_id: "message-1",
  part_id: "call-1",
} as const;

const FAILURE = {
  class: "unknown",
  connector_id: null,
  source: "Sourcecado action",
  retry_safe: false,
  idempotent: false,
  summary: "Sourcecado action failed.",
  repair_route: null,
  detail: "denied by user",
  call_id: "call-1",
  run_id: "run-1",
  session_id: "t",
  state: "failed",
} as const;

function frames(decision: "denied" | "allowed") {
  return [
    { event_id: "e-1", type: "turn_start", state: "running" },
    {
      event_id: "e-2",
      type: "permission_required",
      id: "call-1",
      name: "board_delete",
      arguments: { person_id: "person_qa_does_not_exist" },
      reason: "consequential: waits for Allow or Deny",
    },
    {
      event_id: "e-3",
      type: "tool_finished",
      id: "call-1",
      name: "board_delete",
      ok: false,
      result: { error: decision === "denied" ? "denied by user" : "unknown person" },
      failure: FAILURE,
    },
    {
      event_id: "e-4",
      type: "approval_resolved",
      id: "call-1",
      name: "board_delete",
      resolution: decision,
      decision: decision === "denied" ? "deny" : "allow",
      actor: "operator",
      requested_at: "2026-10-26T12:00:00Z",
      resolved_at: "2026-10-26T12:00:05Z",
      scope: "once",
      execution_status: decision === "denied" ? "not_run" : "failed",
      execution_error: null,
    },
  ] as const;
}

function render1(store: SourcecadoChatStore) {
  const [message] = store.messagesFor("t");
  const converted = convertStructuredMessage(message as never) as {
    content: readonly { type: string }[];
    metadata?: { custom?: { sourcecadoState?: string } };
  };
  const tools = converted.content.filter(
    (part): part is ToolCallMessagePart => part.type === "tool-call",
  );
  return { tools, state: converted.metadata?.custom?.sourcecadoState };
}

describe("a denied approval, frame by frame", () => {
  afterEach(() => vi.useRealTimers());

  it("never renders Failed or Completed for a denied call, including the 1.5 second gap", () => {
    vi.useFakeTimers();
    const store = new SourcecadoChatStore([{ id: "t", messages: [] }], "t");
    const seen: string[] = [];
    let view: ReturnType<typeof render> | null = null;

    const snapshot = (label: string) => {
      const { tools, state } = render1(store);
      const element = (
        <ActivityGroup tools={tools} messageState={state} />
      );
      if (view) view.rerender(element);
      else view = render(element);
      seen.push(`${label}: ${view.container.textContent}`);
      return tools[0];
    };

    const apply = (event: Record<string, unknown>) =>
      act(() => {
        store.applyChatEvent(
          parseChatEvent({ ...envelope, ...event } as never) as never,
        );
      });

    const [start, permission, finished, resolved] = frames("denied");
    apply(start);
    apply(permission);
    snapshot("pending");

    // tool_finished ok=false lands first. The approval is still undecided.
    apply(finished);
    const waiting = snapshot("failed result, no decision yet");
    expect(waiting.isError).toBeFalsy();
    expect(waiting.result).toBeUndefined();
    expect(
      (waiting.providerMetadata as { sourcecado: { failure: unknown } }).sourcecado.failure,
    ).toBeNull();
    expect(view!.container).toHaveTextContent(/Running/);

    act(() => {
      vi.advanceTimersByTime(1500);
    });
    snapshot("1.5 seconds later, still no decision");

    apply(resolved);
    const denied = snapshot("denied");
    expect(denied.approval).toMatchObject({ approved: false });
    expect(view!.container).toHaveTextContent(/Denied/);

    for (const line of seen) {
      expect(line, line).not.toMatch(/Failed|Completed/);
    }
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("still shows Failed for an allowed call that failed, once the decision is in", () => {
    const store = new SourcecadoChatStore([{ id: "t", messages: [] }], "t");
    const apply = (event: Record<string, unknown>) =>
      store.applyChatEvent(
        parseChatEvent({ ...envelope, ...event } as never) as never,
      );
    const [start, permission, finished, resolved] = frames("allowed");
    apply(start);
    apply(permission);
    apply(finished);

    // Undecided: held back, not failed.
    const held = render1(store);
    const { container, rerender } = render(
      <ActivityGroup tools={held.tools} messageState={held.state} />,
    );
    expect(container).not.toHaveTextContent(/Failed/);

    apply(resolved);
    const after = render1(store);
    rerender(<ActivityGroup tools={after.tools} messageState={after.state} />);
    expect(container).toHaveTextContent(/Failed/);
  });
});
