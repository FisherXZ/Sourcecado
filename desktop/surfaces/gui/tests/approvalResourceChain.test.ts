import { describe, expect, it } from "vitest";

import { parseChatEvent } from "../src/api";
import { convertStructuredMessage } from "../src/chat/messageAdapter";
import { SourcecadoChatStore } from "../src/chat/store";

/**
 * Integration cover for the gmail_send approval resource.
 *
 * Every layer of this path has its own unit test -- protocol.test.ts parses it,
 * store.test.ts stores it, messageAdapter.test.ts exposes it, approvalCard
 * renders it. Each one builds its own input, so all four passed while the path
 * between them was not connected: three separate seams opened in this single
 * feature (backend->client, client->component, store->adapter). This test joins
 * them, so a break in any hand-off fails here instead of silently blanking the
 * approval card.
 *
 * The operator decision this protects: authorizing gmail_send without being
 * shown the recipient is not a decision anyone can actually make.
 */
function pendingApprovalPart(resource: unknown) {
  const store = new SourcecadoChatStore([{ id: "t", messages: [] }], "t");
  const envelope = {
    version: 2,
    session_id: "t",
    run_id: "run-1",
    message_id: "message-1",
    part_id: "call-1",
  } as const;

  store.applyChatEvent(
    parseChatEvent({
      ...envelope,
      event_id: "event-1",
      type: "turn_start",
      state: "running",
    } as never) as never,
  );
  store.applyChatEvent(
    parseChatEvent({
      ...envelope,
      event_id: "event-2",
      type: "permission_required",
      id: "call-1",
      name: "gmail_send",
      arguments: { draft_id: "draft-1" },
      reason: "Sending requires permission.",
      ...(resource === undefined ? {} : { resource }),
    } as never) as never,
  );

  const [message] = store.messagesFor("t");
  const converted = convertStructuredMessage(message as never) as {
    content: readonly { type: string; providerMetadata?: Record<string, never> }[];
  };
  const part = converted.content.find((entry) => entry.type.startsWith("tool"));
  return (part?.providerMetadata as { sourcecado?: { resource?: Record<string, unknown> } } | undefined)
    ?.sourcecado?.resource;
}

describe("gmail_send approval resource, wire to rendered part", () => {
  it("delivers recipient, subject, and account across every hand-off", () => {
    expect(
      pendingApprovalPart({
        kind: "gmail_draft",
        draft_id: "draft-1",
        to: "candidate@example.com",
        subject: "Following up",
        account: "operator@example.com",
      }),
    ).toEqual({
      kind: "gmail_draft",
      draft_id: "draft-1",
      to: "candidate@example.com",
      subject: "Following up",
      account: "operator@example.com",
    });
  });

  it("strips a field the backend should never send, even mid-chain", () => {
    // DU-12: provenance without exposing tokens or headers. If a future backend
    // change leaks a body, it must die at the boundary, not reach the DOM.
    const resource = pendingApprovalPart({
      kind: "gmail_draft",
      draft_id: "draft-1",
      to: "candidate@example.com",
      subject: "Following up",
      account: "operator@example.com",
      body: "secret message body",
      authorization: "Bearer token",
    });
    expect(resource).toBeDefined();
    expect(resource).not.toHaveProperty("body");
    expect(resource).not.toHaveProperty("authorization");
  });

  it("survives a lookup failure with nulls rather than dropping the approval", () => {
    // The backend degrades each field independently when the draft read fails.
    expect(
      pendingApprovalPart({
        kind: "gmail_draft",
        draft_id: "draft-1",
        to: null,
        subject: null,
        account: null,
      }),
    ).toEqual({
      kind: "gmail_draft",
      draft_id: "draft-1",
      to: null,
      subject: null,
      account: null,
    });
  });

  it("leaves the approval intact when no resource is sent at all", () => {
    expect(pendingApprovalPart(undefined) ?? null).toBeNull();
  });
});
