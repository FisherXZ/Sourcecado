import { describe, expect, it } from "vitest";

import { convertStructuredMessage } from "../src/chat/messageAdapter";

describe("convertStructuredMessage", () => {
  it("preserves the proposed structured text identity and completion state", () => {
    expect(
      convertStructuredMessage({
        id: "message-answer-1",
        role: "assistant",
        state: "complete",
        parts: [
          {
            type: "text",
            id: "part-answer-1",
            text: "Three leads match the brief.",
            state: "complete",
          },
        ],
      }),
    ).toEqual({
      id: "message-answer-1",
      role: "assistant",
      content: [
        {
          type: "text",
          text: "Three leads match the brief.",
          parentId: "part-answer-1",
          status: { type: "complete" },
        },
      ],
      status: { type: "complete", reason: "stop" },
      metadata: {
        custom: {
          sourcecadoState: "complete",
          sourcecadoPartIds: ["part-answer-1"],
        },
      },
    });
  });

  it("maps a structured tool result without changing its tool-call identity", () => {
    expect(
      convertStructuredMessage({
        id: "message-tools-1",
        role: "assistant",
        state: "complete",
        parts: [
          {
            type: "tool",
            id: "call-drive-1",
            name: "drive_search",
            arguments: { query: "Codeology recruiting" },
            state: "complete",
            result: { documents: 2 },
          },
        ],
      }),
    ).toEqual({
      id: "message-tools-1",
      role: "assistant",
      content: [
        {
          type: "tool-call",
          toolCallId: "call-drive-1",
          toolName: "drive_search",
          args: { query: "Codeology recruiting" },
          argsText: '{"query":"Codeology recruiting"}',
          result: { documents: 2 },
          isError: false,
        },
      ],
      status: { type: "complete", reason: "stop" },
      metadata: {
        custom: {
          sourcecadoState: "complete",
          sourcecadoPartIds: ["call-drive-1"],
        },
      },
    });
  });

  it("represents a pending Sourcecado approval on its existing tool part", () => {
    expect(
      convertStructuredMessage({
        id: "message-approval-1",
        role: "assistant",
        state: "waiting-approval",
        parts: [
          {
            type: "tool",
            id: "call-gmail-1",
            name: "gmail_create_draft",
            arguments: { to: "alyssa@example.com" },
            state: "running",
            approval: {
              id: "approval-gmail-1",
              state: "pending",
              reason: "Creating a draft is a write action.",
            },
          },
        ],
      }),
    ).toEqual({
      id: "message-approval-1",
      role: "assistant",
      content: [
        {
          type: "tool-call",
          toolCallId: "call-gmail-1",
          toolName: "gmail_create_draft",
          args: { to: "alyssa@example.com" },
          argsText: '{"to":"alyssa@example.com"}',
          isError: false,
          approval: {
            id: "approval-gmail-1",
            reason: "Creating a draft is a write action.",
          },
          providerMetadata: {
            sourcecado: {
              approvalState: "pending",
              failure: null,
              recovery: null,
              actor: null,
              requestedAt: null,
              resolvedAt: null,
              scope: null,
              executionStatus: null,
              executionError: null,
              resource: null,
            },
          },
        },
      ],
      status: { type: "requires-action", reason: "tool-calls" },
      metadata: {
        custom: {
          sourcecadoState: "waiting-approval",
          sourcecadoPartIds: ["call-gmail-1"],
        },
      },
    });
  });

  it("keeps successful work visible while marking a structured turn partial", () => {
    const converted = convertStructuredMessage({
      id: "message-partial-1",
      role: "assistant",
      state: "partial",
      parts: [
        {
          type: "tool",
          id: "call-drive-ok",
          name: "drive_search",
          arguments: { query: "Codeology" },
          state: "complete",
          result: { documents: 2 },
        },
        {
          type: "tool",
          id: "call-notion-failed",
          name: "notion_search",
          arguments: { query: "Codeology" },
          state: "error",
          result: { error: "connection expired" },
        },
      ],
    });

    expect(converted.status).toEqual({ type: "incomplete", reason: "other" });
    expect(converted.metadata?.custom?.sourcecadoState).toBe("partial");
    expect(converted.metadata?.custom?.sourcecadoPartIds).toEqual([
      "call-drive-ok",
      "call-notion-failed",
    ]);
    expect(converted.content).toEqual([
      expect.objectContaining({
        toolCallId: "call-drive-ok",
        result: { documents: 2 },
        isError: false,
      }),
      expect.objectContaining({
        toolCallId: "call-notion-failed",
        result: { error: "connection expired" },
        isError: true,
      }),
    ]);
  });

  it("keeps interrupted text and marks both message and part cancelled", () => {
    const converted = convertStructuredMessage({
      id: "message-cancelled-1",
      role: "assistant",
      state: "cancelled",
      parts: [
        {
          type: "text",
          id: "part-cancelled-1",
          text: "I found two likely",
          state: "cancelled",
        },
      ],
    });

    expect(converted).toEqual({
      id: "message-cancelled-1",
      role: "assistant",
      content: [
        {
          type: "text",
          text: "I found two likely",
          parentId: "part-cancelled-1",
          status: { type: "incomplete", reason: "cancelled" },
        },
      ],
      status: { type: "incomplete", reason: "cancelled" },
      metadata: {
        custom: {
          sourcecadoState: "cancelled",
          sourcecadoPartIds: ["part-cancelled-1"],
        },
      },
    });
  });

  it("marks a live text snapshot as running without changing its identities", () => {
    const converted = convertStructuredMessage({
      id: "message-live-1",
      role: "assistant",
      state: "running",
      parts: [
        {
          type: "text",
          id: "part-live-1",
          text: "Searching",
          state: "running",
        },
      ],
    });

    expect(converted.status).toEqual({ type: "running" });
    expect(converted.content).toEqual([
      {
        type: "text",
        text: "Searching",
        parentId: "part-live-1",
        status: { type: "running" },
      },
    ]);
    expect(converted.metadata?.custom?.sourcecadoPartIds).toEqual([
      "part-live-1",
    ]);
  });
});

describe("convertStructuredMessage approval resource", () => {
  const toolPart = {
    type: "tool" as const,
    id: "call-1",
    name: "gmail_send",
    arguments: { draft_id: "draft-1" },
    state: "running" as const,
  };

  it("exposes the approval resource at providerMetadata.sourcecado.resource", () => {
    const converted = convertStructuredMessage({
      id: "message-1",
      role: "assistant",
      state: "waiting-approval",
      parts: [
        {
          ...toolPart,
          approval: {
            id: "call-1",
            state: "pending",
            reason: "Sending requires permission.",
            resource: {
              kind: "gmail_draft",
              draft_id: "draft-1",
              to: "a@example.com",
              subject: "Hello",
              account: "me@example.com",
            },
          },
        },
      ],
    });

    const [part] = converted.content as ReadonlyArray<{
      providerMetadata?: { sourcecado?: Record<string, unknown> };
    }>;
    expect(part?.providerMetadata?.sourcecado?.resource).toEqual({
      kind: "gmail_draft",
      draft_id: "draft-1",
      to: "a@example.com",
      subject: "Hello",
      account: "me@example.com",
    });
  });

  it("exposes null when the approval has no resource", () => {
    const converted = convertStructuredMessage({
      id: "message-1",
      role: "assistant",
      state: "waiting-approval",
      parts: [
        {
          ...toolPart,
          approval: {
            id: "call-1",
            state: "pending",
            reason: "Sending requires permission.",
          },
        },
      ],
    });

    const [part] = converted.content as ReadonlyArray<{
      providerMetadata?: { sourcecado?: Record<string, unknown> };
    }>;
    expect(part?.providerMetadata?.sourcecado?.resource).toBeNull();
  });
});
