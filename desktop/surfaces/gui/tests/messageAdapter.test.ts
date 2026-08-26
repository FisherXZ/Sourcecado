import { describe, expect, it } from "vitest";

import {
  convertLegacyTranscript,
  convertStructuredMessage,
} from "../src/chat/messageAdapter";

describe("convertLegacyTranscript", () => {
  it("converts legacy text records with replay-stable message and part identities", () => {
    const records = [
      { role: "user", content: "Find recruiting leads." },
      { role: "assistant", content: "I found three leads." },
    ] as const;

    const live = convertLegacyTranscript("thread-alpha", records);
    const restored = convertLegacyTranscript("thread-alpha", records);

    expect(live).toEqual([
      {
        id: "thread-alpha:legacy:0",
        role: "user",
        content: [
          {
            type: "text",
            text: "Find recruiting leads.",
            parentId: "thread-alpha:legacy:0:text:0",
          },
        ],
      },
      {
        id: "thread-alpha:legacy:1",
        role: "assistant",
        content: [
          {
            type: "text",
            text: "I found three leads.",
            parentId: "thread-alpha:legacy:1:text:0",
          },
        ],
        status: { type: "complete", reason: "stop" },
      },
    ]);
    expect(restored).toEqual(live);
  });

  it("joins legacy tool calls and results into one stable assistant tool part", () => {
    const records = [
      {
        role: "assistant",
        content: null,
        tool_calls: [
          {
            id: "call-apollo-1",
            function: {
              name: "apollo_search_people",
              arguments: '{"title":"Recruiter"}',
            },
          },
        ],
      },
      {
        role: "tool",
        tool_call_id: "call-apollo-1",
        name: "apollo_search_people",
        content: '{"people":3}',
      },
    ] as const;

    expect(convertLegacyTranscript("thread-alpha", records)).toEqual([
      {
        id: "thread-alpha:legacy:0",
        role: "assistant",
        content: [
          {
            type: "tool-call",
            toolCallId: "call-apollo-1",
            toolName: "apollo_search_people",
            args: { title: "Recruiter" },
            argsText: '{"title":"Recruiter"}',
            result: { people: 3 },
            isError: false,
          },
        ],
        status: { type: "complete", reason: "stop" },
      },
    ]);
  });
});

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
