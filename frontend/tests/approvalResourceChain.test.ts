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
function pendingApprovalPart(
  resource: unknown,
  name = "gmail_send",
  args: Record<string, unknown> = { draft_id: "draft-1" },
) {
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
      name,
      arguments: args,
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

describe("person-bound approval resources, wire to rendered part", () => {
  it("keeps exactly the send authority fields and drops the rest", () => {
    // Shape: backend/coworker/gmail.py SendAuthority.as_resource.
    expect(
      pendingApprovalPart({
        kind: "gmail_send_authority",
        person_id: "person_8f2a",
        draft_id: "draft-1",
        account: "operator@example.com",
        to: "candidate@example.com",
        subject: "Following up",
        body_digest: "3f7a9c1e5b2d",
        sent: false,
        body: "secret message body",
      }),
    ).toEqual({
      kind: "gmail_send_authority",
      person_id: "person_8f2a",
      draft_id: "draft-1",
      account: "operator@example.com",
      to: "candidate@example.com",
      subject: "Following up",
      body_digest: "3f7a9c1e5b2d",
    });
  });

  it("keeps exactly the enrichment fields and drops the rest", () => {
    // Shape: backend/coworker/apollo.py enrichment_resource.
    expect(
      pendingApprovalPart(
        {
          kind: "apollo_enrichment",
          person_id: "person_8f2a",
          person: "Maya Chen",
          title: "Founder",
          company: "Acme",
          matched_on: "email",
          credits: 1,
          reason: "Spends 1 Apollo credit to look up Maya Chen by email.",
          email: "maya@example.com",
        },
        "apollo_enrich_contact",
        { firstName: "Maya", lastName: "Chen" },
      ),
    ).toEqual({
      kind: "apollo_enrichment",
      person_id: "person_8f2a",
      person: "Maya Chen",
      title: "Founder",
      company: "Acme",
      matched_on: "email",
      credits: 1,
    });
  });

  it("drops an enrichment resource with a cost that is not a number", () => {
    expect(
      pendingApprovalPart(
        {
          kind: "apollo_enrichment",
          person_id: "person_8f2a",
          person: "Maya Chen",
          matched_on: "email",
          credits: "one",
        },
        "apollo_enrich_contact",
        { firstName: "Maya", lastName: "Chen" },
      ) ?? null,
    ).toBeNull();
  });
});

function resolvedToolPart(decision: "denied" | "allowed", toolOk: boolean) {
  const store = new SourcecadoChatStore([{ id: "t", messages: [] }], "t");
  const envelope = {
    version: 2,
    session_id: "t",
    run_id: "run-1",
    message_id: "message-1",
    part_id: "call-1",
  } as const;
  const apply = (event: Record<string, unknown>) =>
    store.applyChatEvent(parseChatEvent({ ...envelope, ...event } as never) as never);

  apply({ event_id: "e-1", type: "turn_start", state: "running" });
  apply({
    event_id: "e-2",
    type: "permission_required",
    id: "call-1",
    name: "gmail_send",
    arguments: { draft_id: "draft-1" },
    reason: "Sending requires permission.",
    resource: {
      kind: "gmail_draft",
      draft_id: "draft-1",
      to: "candidate@example.com",
      subject: "Following up",
      account: "operator@example.com",
    },
  });
  // The backend closes a denied call with ok=false and a failure record
  // before it writes the approval receipt (turn.py).
  apply({
    event_id: "e-3",
    type: "tool_finished",
    id: "call-1",
    name: "gmail_send",
    ok: toolOk,
    result: toolOk ? { sent: true } : { error: "denied by user" },
    ...(toolOk
      ? {}
      : {
          failure: {
            class: "unknown",
            connector_id: "gmail",
            source: "Gmail",
            retry_safe: false,
            idempotent: false,
            summary: "Gmail failed.",
            repair_route: null,
            detail: "denied by user",
            call_id: "call-1",
            run_id: "run-1",
            session_id: "t",
            state: "failed",
          },
        }),
  });
  apply({
    event_id: "e-4",
    type: "approval_resolved",
    id: "call-1",
    name: "gmail_send",
    resolution: decision,
    decision: decision === "denied" ? "deny" : "allow",
    actor: "operator",
    requested_at: "2026-10-26T12:00:00Z",
    resolved_at: "2026-10-26T12:00:05Z",
    scope: "once",
    execution_status: decision === "denied" ? "not_run" : "failed",
    execution_error: null,
  });

  const [message] = store.messagesFor("t");
  const converted = convertStructuredMessage(message as never) as {
    content: readonly Record<string, unknown>[];
  };
  const part = converted.content.find((entry) => entry.type === "tool-call") as {
    isError: boolean;
    approval?: { approved?: boolean };
    providerMetadata: { sourcecado: Record<string, unknown> };
  };
  return part;
}

describe("a resolved approval, store to rendered part", () => {
  it("keeps the resolved request so the receipt can still name the recipient", () => {
    const part = resolvedToolPart("denied", false);

    expect(part.providerMetadata.sourcecado.resource).toMatchObject({
      kind: "gmail_draft",
      to: "candidate@example.com",
    });
  });

  it("does not turn a denial into a failed step", () => {
    const part = resolvedToolPart("denied", false);

    expect(part.approval?.approved).toBe(false);
    expect(part.isError).toBe(false);
    expect(part.providerMetadata.sourcecado.failure).toBeNull();
  });

  it("still reports a failure for an allowed call that failed", () => {
    const part = resolvedToolPart("allowed", false);

    expect(part.approval?.approved).toBe(true);
    expect(part.isError).toBe(true);
    expect(part.providerMetadata.sourcecado.failure).not.toBeNull();
  });
});
