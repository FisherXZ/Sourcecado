import { fireEvent, render, screen } from "@testing-library/react";
import {
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useAuiState,
  type AddToolResultOptions,
  type AppendMessage,
  type ExternalThreadQueueAdapter,
  type RespondToToolApprovalOptions,
  type ToolCallMessagePartProps,
} from "@assistant-ui/react";
import { describe, expect, it, vi } from "vitest";

import type { SourcecadoStructuredMessage } from "../src/chat/messageAdapter";
import { SourcecadoQueuePolicy } from "../src/chat/queuePolicy";
import { SourcecadoRuntimeProvider } from "../src/chat/SourcecadoRuntimeProvider";

const queuedMessage = (text: string): AppendMessage => ({
  role: "user",
  content: [{ type: "text", text }],
  attachments: [],
  createdAt: new Date(0),
  metadata: { custom: {} },
  parentId: null,
  sourceId: null,
  runConfig: undefined,
});

function RuntimeText() {
  const part = useAuiState((state) => state.part);
  if (part.type !== "text") return null;
  const partId = part.parentId ?? "text";
  return (
    <span data-testid={`part-${partId}`} data-part-id={partId}>
      {part.text}
    </span>
  );
}

function toolState(part: ToolCallMessagePartProps): string {
  if (part.approval?.resolution) return `approval-${part.approval.resolution}`;
  if (part.approval?.approved === true) return "approval-allowed";
  if (part.approval?.approved === false) return "approval-denied";
  if (part.approval) return "approval-pending";
  if (part.isError) return "error";
  if (part.result !== undefined) return "complete";
  return "running";
}

function RuntimeTool(part: ToolCallMessagePartProps) {
  return (
    <section
      data-testid={`part-${part.toolCallId}`}
      data-part-id={part.toolCallId}
      data-tool-name={part.toolName}
      data-tool-state={toolState(part)}
    >
      {part.toolName}
      {!part.approval && part.result === undefined ? (
        <button type="button" onClick={() => part.addResult({ proof: true })}>
          Record {part.toolName} test result
        </button>
      ) : null}
      {part.approval &&
      part.approval.approved === undefined &&
      part.approval.resolution === undefined ? (
        <button
          type="button"
          onClick={() => part.respondToApproval({ approved: true })}
        >
          Allow {part.toolName} once
        </button>
      ) : null}
    </section>
  );
}

function RuntimeMessage() {
  const message = useAuiState((state) => state.message);
  const messageState =
    message.role === "assistant" ? message.status.type : "complete";
  return (
    <article
      data-testid={`message-${message.id}`}
      data-message-id={message.id}
      data-message-state={messageState}
    >
      <MessagePrimitive.Parts
        components={{ Text: RuntimeText, tools: { Fallback: RuntimeTool } }}
      />
    </article>
  );
}

function RuntimeHarness({
  messages,
  onAddToolResult,
  onRespondToToolApproval,
  queue,
  onCancel,
}: {
  readonly messages: readonly SourcecadoStructuredMessage[];
  readonly onAddToolResult: (options: AddToolResultOptions) => void;
  readonly onRespondToToolApproval: (
    options: RespondToToolApprovalOptions,
  ) => void;
  readonly queue?: ExternalThreadQueueAdapter;
  readonly onCancel?: () => Promise<void>;
}) {
  return (
    <SourcecadoRuntimeProvider
      messages={messages}
      running={messages.some((message) => message.state === "running")}
      onNew={vi.fn()}
      onAddToolResult={onAddToolResult}
      onRespondToToolApproval={onRespondToToolApproval}
      queue={queue}
      onCancel={onCancel}
    >
      <ThreadPrimitive.Messages
        components={{ Message: RuntimeMessage }}
      />
      {onCancel ? (
        <ComposerPrimitive.Root>
          <ComposerPrimitive.Cancel>Stop test run</ComposerPrimitive.Cancel>
        </ComposerPrimitive.Root>
      ) : null}
    </SourcecadoRuntimeProvider>
  );
}

describe("SourcecadoRuntimeProvider", () => {
  it("renders stable Sourcecado identities through the production runtime", () => {
    render(
      <RuntimeHarness
        messages={[
          {
            id: "message-runtime-1",
            role: "assistant",
            state: "waiting-approval",
            parts: [
              {
                type: "text",
                id: "part-runtime-text",
                text: "I can create that draft.",
                state: "complete",
              },
              {
                type: "tool",
                id: "call-runtime-tool",
                name: "gmail_create_draft",
                arguments: { to: "alyssa@example.com" },
                state: "running",
                approval: {
                  id: "approval-runtime-tool",
                  state: "pending",
                  reason: "Draft creation requires permission.",
                },
              },
            ],
          },
        ]}
        onAddToolResult={vi.fn()}
        onRespondToToolApproval={vi.fn()}
      />,
    );

    expect(screen.getByTestId("message-message-runtime-1")).toHaveAttribute(
      "data-message-id",
      "message-runtime-1",
    );
    expect(screen.getByTestId("message-message-runtime-1")).toHaveAttribute(
      "data-message-state",
      "requires-action",
    );
    expect(screen.getByTestId("part-part-runtime-text")).toHaveAttribute(
      "data-part-id",
      "part-runtime-text",
    );
    expect(screen.getByTestId("part-call-runtime-tool")).toHaveAttribute(
      "data-tool-state",
      "approval-pending",
    );
  });

  it("routes tool-result and approval actions through the addressed runtime part", () => {
    const onAddToolResult = vi.fn();
    const onRespondToToolApproval = vi.fn();
    render(
      <RuntimeHarness
        messages={[
          {
            id: "message-actions-1",
            role: "assistant",
            state: "waiting-approval",
            parts: [
              {
                type: "tool",
                id: "call-result-target",
                name: "drive_search",
                arguments: { query: "Codeology" },
                state: "running",
              },
              {
                type: "tool",
                id: "call-approval-target",
                name: "gmail_create_draft",
                arguments: { to: "alyssa@example.com" },
                state: "running",
                approval: { id: "approval-target", state: "pending" },
              },
            ],
          },
        ]}
        onAddToolResult={onAddToolResult}
        onRespondToToolApproval={onRespondToToolApproval}
      />,
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Record drive_search test result" }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Allow gmail_create_draft once" }),
    );

    expect(onAddToolResult).toHaveBeenCalledWith({
      messageId: "message-actions-1",
      toolCallId: "call-result-target",
      toolName: "drive_search",
      result: { proof: true },
      isError: false,
    });
    expect(onRespondToToolApproval).toHaveBeenCalledWith({
      approvalId: "approval-target",
      approved: true,
      reason: undefined,
    });
  });

  it("pauses the host queue before production-runtime cancellation settles", () => {
    const dispatch = vi.fn();
    const cancelTransport = vi.fn();
    const policy = new SourcecadoQueuePolicy({ dispatch, cancelTransport });
    policy.beginRun();
    policy.enqueue(queuedMessage("queued after cancel"));
    render(
      <RuntimeHarness
        messages={[
          {
            id: "message-running-1",
            role: "assistant",
            state: "running",
            parts: [
              {
                type: "text",
                id: "part-running-1",
                text: "Working",
                state: "running",
              },
            ],
          },
        ]}
        onAddToolResult={vi.fn()}
        onRespondToToolApproval={vi.fn()}
        queue={policy.adapter}
        onCancel={async () => policy.cancelRun()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Stop test run" }));
    policy.settleRun();

    expect(cancelTransport).toHaveBeenCalledOnce();
    expect(dispatch).not.toHaveBeenCalled();
    expect(policy.pendingPrompts()).toEqual(["queued after cancel"]);
  });
});
