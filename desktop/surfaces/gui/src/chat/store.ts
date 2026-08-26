import type {
  AddToolResultOptions,
  RespondToToolApprovalOptions,
} from "@assistant-ui/react";

import {
  parseChatEvent,
  type ChatEvent,
  type ProtocolChatEvent,
  type RecoverableChatNotice,
} from "./protocol";
import type { SourcecadoStructuredMessage } from "./messageAdapter";
import type {
  ProvenanceArtifact,
  ProvenanceSource,
  ToolFailure,
} from "./protocol";

function isRecoverableNotice(
  event: unknown,
): event is RecoverableChatNotice {
  if (typeof event !== "object" || event === null || "version" in event) {
    return false;
  }
  const candidate = event as Record<string, unknown>;
  const notice = candidate.notice;
  return (
    candidate.type === "error" &&
    typeof candidate.message === "string" &&
    typeof notice === "object" &&
    notice !== null &&
    (notice as Record<string, unknown>).recoverable === true &&
    ["malformed_event", "unsupported_version"].includes(
      String((notice as Record<string, unknown>).code),
    )
  );
}

export type SourcecadoThreadFixture = {
  readonly id: string;
  readonly messages: readonly SourcecadoStructuredMessage[];
};

export type SourcecadoEvent =
  | {
      readonly type: "message_started";
      readonly threadId: string;
      readonly messageId: string;
      readonly role: SourcecadoStructuredMessage["role"];
    }
  | {
      readonly type: "text_delta";
      readonly threadId: string;
      readonly messageId: string;
      readonly partId: string;
      readonly delta: string;
    }
  | {
      readonly type: "tool_started";
      readonly threadId: string;
      readonly messageId: string;
      readonly toolCallId: string;
      readonly name: string;
      readonly arguments: Readonly<Record<string, unknown>>;
      readonly startedAt?: number;
    }
  | {
      readonly type: "tool_result";
      readonly threadId: string;
      readonly messageId: string;
      readonly toolCallId: string;
      readonly result: unknown;
      readonly isError: boolean;
      readonly completedAt?: number;
      readonly failure?: ToolFailure;
      readonly sources?: readonly ProvenanceSource[];
      readonly artifacts?: readonly ProvenanceArtifact[];
    }
  | {
      readonly type: "tool_recovery";
      readonly threadId: string;
      readonly messageId: string;
      readonly toolCallId: string;
      readonly commandId: string;
      readonly action: "retry" | "repair" | "continue";
      readonly status: string;
      readonly outcome?: string;
      readonly repairRoute?: string | null;
      readonly failure?: ToolFailure;
    }
  | {
      readonly type: "approval_required";
      readonly threadId: string;
      readonly messageId: string;
      readonly toolCallId: string;
      readonly approvalId: string;
      readonly reason: string;
      readonly requestedAt?: string;
      readonly scope?: string;
    }
  | {
      readonly type: "approval_resolved";
      readonly threadId: string;
      readonly messageId: string;
      readonly toolCallId: string;
      readonly state: "allowed" | "denied" | "expired" | "cancelled";
      readonly actor: string | null;
      readonly requestedAt: string;
      readonly resolvedAt: string;
      readonly scope: string;
      readonly executionStatus: string;
      readonly executionError: string | null;
    }
  | {
      readonly type: "message_state_changed";
      readonly threadId: string;
      readonly messageId: string;
      readonly state:
        | "stopping"
        | "waiting-approval"
        | "partial"
        | "cancelled"
        | "failed"
        | "stopped"
        | "interrupted";
    }
  | {
      readonly type: "message_finished";
      readonly threadId: string;
      readonly messageId: string;
      readonly state: "complete";
    };

export class SourcecadoChatStore {
  private readonly threads = new Map<
    string,
    readonly SourcecadoStructuredMessage[]
  >();
  private readonly seenEventIds = new Set<string>();
  private noticeNumber = 0;
  private activeThreadId: string;

  constructor(
    fixtures: readonly SourcecadoThreadFixture[],
    activeThreadId: string,
  ) {
    this.activeThreadId = activeThreadId;
    for (const fixture of fixtures) {
      this.threads.set(fixture.id, fixture.messages);
    }
  }

  messagesFor(threadId: string): readonly SourcecadoStructuredMessage[] {
    return this.threads.get(threadId) ?? [];
  }

  activeMessages(): readonly SourcecadoStructuredMessage[] {
    return this.messagesFor(this.activeThreadId);
  }

  selectThread(threadId: string): void {
    this.activeThreadId = threadId;
  }

  replaceThread(
    threadId: string,
    messages: readonly SourcecadoStructuredMessage[],
  ): void {
    this.threads.set(threadId, messages);
  }

  applyChatEvent(value: unknown): ChatEvent {
    const event = isRecoverableNotice(value) ? value : parseChatEvent(value);
    if (!("version" in event)) {
      if (isRecoverableNotice(event)) this.applyNotice(event);
      return event;
    }
    if (this.seenEventIds.has(event.event_id)) return event;
    this.seenEventIds.add(event.event_id);
    this.applyProtocolEvent(event);
    return event;
  }

  private applyNotice(event: RecoverableChatNotice): void {
    const threadId = event.session_id ?? this.activeThreadId;
    const messageId = `${threadId}:notice:${++this.noticeNumber}`;
    this.threads.set(threadId, [
      ...this.messagesFor(threadId),
      {
        id: messageId,
        role: "assistant",
        state: "partial",
        parts: [
          {
            type: "notice",
            id: `${messageId}:part`,
            code: event.notice.code,
            message: event.message,
            recoverable: event.notice.recoverable,
          },
        ],
      },
    ]);
  }

  replayChatEvents(events: readonly unknown[]): void {
    const touched = new Map<string, Set<string>>();
    for (const value of events) {
      const event = this.applyChatEvent(value);
      if (!("version" in event)) continue;
      const messages = touched.get(event.session_id) ?? new Set<string>();
      messages.add(event.message_id);
      touched.set(event.session_id, messages);
    }
    for (const [threadId, messageIds] of touched) {
      for (const message of this.messagesFor(threadId)) {
        if (message.state !== "running" || !messageIds.has(message.id)) continue;
        this.apply({
          type: "message_state_changed",
          threadId,
          messageId: message.id,
          state: "interrupted",
        });
      }
    }
  }

  private applyProtocolEvent(event: ProtocolChatEvent): void {
    if (event.type === "turn_start") {
      this.apply({
        type: "message_started",
        threadId: event.session_id,
        messageId: event.message_id,
        role: "assistant",
      });
      return;
    }
    if (event.type === "assistant_delta") {
      this.apply({
        type: "text_delta",
        threadId: event.session_id,
        messageId: event.message_id,
        partId: event.part_id,
        delta: event.delta,
      });
      return;
    }
    if (event.type === "turn_stopping") {
      this.apply({
        type: "message_state_changed",
        threadId: event.session_id,
        messageId: event.message_id,
        state: "stopping",
      });
      return;
    }
    if (event.type === "turn_stopped") {
      this.apply({
        type: "message_state_changed",
        threadId: event.session_id,
        messageId: event.message_id,
        state: "cancelled",
      });
      return;
    }
    if (event.type === "permission_required") {
      this.apply({
        type: "tool_started",
        threadId: event.session_id,
        messageId: event.message_id,
        toolCallId: event.id,
        name: event.name,
        arguments: event.arguments,
      });
      this.apply({
        type: "approval_required",
        threadId: event.session_id,
        messageId: event.message_id,
        toolCallId: event.id,
        approvalId: event.id,
        reason: event.reason,
        ...(event.requested_at ? { requestedAt: event.requested_at } : {}),
        ...(event.scope ? { scope: event.scope } : {}),
      });
      return;
    }
    if (event.type === "approval_resolved") {
      this.apply({
        type: "approval_resolved",
        threadId: event.session_id,
        messageId: event.message_id,
        toolCallId: event.id,
        state: event.resolution,
        actor: event.actor,
        requestedAt: event.requested_at,
        resolvedAt: event.resolved_at,
        scope: event.scope,
        executionStatus: event.execution_status,
        executionError: event.execution_error,
      });
      return;
    }
    if (event.type === "tool_started") {
      const startedAt = event.started_at
        ? Date.parse(event.started_at)
        : undefined;
      this.apply({
        type: "tool_started",
        threadId: event.session_id,
        messageId: event.message_id,
        toolCallId: event.id,
        name: event.name,
        arguments: event.arguments,
        ...(Number.isFinite(startedAt) ? { startedAt } : {}),
      });
      return;
    }
    if (event.type === "tool_finished") {
      const completedAt = event.finished_at
        ? Date.parse(event.finished_at)
        : undefined;
      this.apply({
        type: "tool_result",
        threadId: event.session_id,
        messageId: event.message_id,
        toolCallId: event.id,
        result: event.result,
        isError: !event.ok,
        ...(Number.isFinite(completedAt) ? { completedAt } : {}),
        ...(event.failure ? { failure: event.failure } : {}),
        ...(event.sources ? { sources: event.sources } : {}),
        ...(event.artifacts ? { artifacts: event.artifacts } : {}),
      });
      return;
    }
    if (event.type === "tool_recovery") {
      this.apply({
        type: "tool_recovery",
        threadId: event.session_id,
        messageId: event.message_id,
        toolCallId: event.call_id,
        commandId: event.command_id,
        action: event.action,
        status: event.status,
        ...(event.outcome ? { outcome: event.outcome } : {}),
        ...(event.repair_route !== undefined
          ? { repairRoute: event.repair_route }
          : {}),
        ...(event.failure ? { failure: event.failure } : {}),
      });
      return;
    }
    if (event.type === "turn_end") {
      if (event.state === "complete") {
        this.apply({
          type: "message_finished",
          threadId: event.session_id,
          messageId: event.message_id,
          state: "complete",
        });
      } else {
        this.apply({
          type: "message_state_changed",
          threadId: event.session_id,
          messageId: event.message_id,
          state: event.state,
        });
      }
      return;
    }
    if (event.type === "error") {
      this.apply({
        type: "message_state_changed",
        threadId: event.session_id,
        messageId: event.message_id,
        state: "failed",
      });
    }
  }

  addToolResult(threadId: string, options: AddToolResultOptions): void {
    this.apply({
      type: "tool_result",
      threadId,
      messageId: options.messageId,
      toolCallId: options.toolCallId,
      result: options.result,
      isError: options.isError,
    });
  }

  respondToApproval(
    threadId: string,
    options: RespondToToolApprovalOptions,
  ): void {
    this.threads.set(
      threadId,
      this.messagesFor(threadId).map((message) => ({
        ...message,
        parts: message.parts.map((part) =>
          part.type === "tool" &&
          part.approval?.id === options.approvalId
            ? {
                ...part,
                approval: {
                  ...part.approval,
                  state: options.approved
                    ? ("allowed" as const)
                    : ("denied" as const),
                  ...(options.reason !== undefined
                    ? { reason: options.reason }
                    : {}),
                },
              }
            : part,
        ),
      })),
    );
  }

  apply(event: SourcecadoEvent): void {
    const messages = this.messagesFor(event.threadId);
    if (event.type === "message_started") {
      this.threads.set(event.threadId, [
        ...messages,
        {
          id: event.messageId,
          role: event.role,
          state: "running",
          parts: [],
        },
      ]);
      return;
    }

    if (event.type === "text_delta") {
      this.threads.set(
        event.threadId,
        messages.map((message) => {
          if (message.id !== event.messageId) return message;
          const current = message.parts.find(
            (part) => part.type === "text" && part.id === event.partId,
          );
          return {
            ...message,
            parts: current
              ? message.parts.map((part) =>
                  part.type === "text" && part.id === event.partId
                    ? { ...part, text: part.text + event.delta }
                    : part,
                )
              : [
                  ...message.parts,
                  {
                    type: "text" as const,
                    id: event.partId,
                    text: event.delta,
                    state: "running" as const,
                  },
                ],
          };
        }),
      );
      return;
    }

    if (event.type === "tool_started") {
      this.threads.set(
        event.threadId,
        messages.map((message) =>
          message.id === event.messageId
            ? {
                ...message,
                parts: message.parts.some(
                  (part) =>
                    part.type === "tool" && part.id === event.toolCallId,
                )
                  ? message.parts.map((part) =>
                      part.type === "tool" && part.id === event.toolCallId
                        ? {
                            ...part,
                            name: event.name,
                            arguments: event.arguments,
                            ...(event.startedAt !== undefined
                              ? { startedAt: event.startedAt }
                              : {}),
                          }
                        : part,
                    )
                  : [
                      ...message.parts,
                      {
                        type: "tool" as const,
                        id: event.toolCallId,
                        name: event.name,
                        arguments: event.arguments,
                        state: "running" as const,
                        ...(event.startedAt !== undefined
                          ? { startedAt: event.startedAt }
                          : {}),
                      },
                    ],
              }
            : message,
        ),
      );
      return;
    }

    if (event.type === "tool_result") {
      this.threads.set(
        event.threadId,
        messages.map((message) =>
          message.id === event.messageId
            ? {
                ...message,
                parts: [
                  ...message.parts.map((part) =>
                    part.type === "tool" && part.id === event.toolCallId
                    ? {
                        ...part,
                        result: event.result,
                        failure: event.failure,
                        state: event.isError
                          ? ("error" as const)
                          : ("complete" as const),
                        ...(event.completedAt !== undefined
                          ? { completedAt: event.completedAt }
                          : {}),
                      }
                    : part,
                  ),
                  ...(event.sources ?? [])
                    .filter(
                      (source) =>
                        !message.parts.some(
                          (part) => part.type === "source" && part.id === source.id,
                        ),
                    )
                    .map((source) => ({ type: "source" as const, ...source })),
                  ...(event.artifacts ?? [])
                    .filter(
                      (artifact) =>
                        !message.parts.some(
                          (part) =>
                            part.type === "artifact" && part.id === artifact.id,
                        ),
                    )
                    .map((artifact) => ({
                      type: "artifact" as const,
                      ...artifact,
                    })),
                ],
              }
            : message,
        ),
      );
      return;
    }

    if (event.type === "tool_recovery") {
      this.threads.set(
        event.threadId,
        messages.map((message) => {
          if (message.id !== event.messageId) return message;
          const parts = message.parts.map((part) =>
            part.type === "tool" && part.id === event.toolCallId
              ? {
                  ...part,
                  ...(event.status === "succeeded"
                    ? { failure: undefined, state: "complete" as const }
                    : event.failure
                      ? { failure: event.failure }
                      : {}),
                  recovery: {
                    commandId: event.commandId,
                    action: event.action,
                    status: event.status,
                    ...(event.outcome ? { outcome: event.outcome } : {}),
                    ...(event.repairRoute !== undefined
                      ? { repairRoute: event.repairRoute }
                      : {}),
                  },
                }
              : part,
          );
          const unresolved = parts.some(
            (part) => part.type === "tool" && part.failure !== undefined,
          );
          return {
            ...message,
            state:
              event.status === "succeeded" && !unresolved
                ? ("complete" as const)
                : ("partial" as const),
            parts,
          };
        }),
      );
      return;
    }

    if (event.type === "approval_required") {
      this.threads.set(
        event.threadId,
        messages.map((message) =>
          message.id === event.messageId
            ? {
                ...message,
                state: "waiting-approval" as const,
                parts: message.parts.map((part) =>
                  part.type === "tool" && part.id === event.toolCallId
                    ? {
                        ...part,
                        approval: {
                          id: event.approvalId,
                          state: "pending" as const,
                          reason: event.reason,
                          ...(event.requestedAt
                            ? { requestedAt: event.requestedAt }
                            : {}),
                          ...(event.scope ? { scope: event.scope } : {}),
                        },
                      }
                    : part,
                ),
              }
            : message,
        ),
      );
      return;
    }

    if (event.type === "approval_resolved") {
      this.threads.set(
        event.threadId,
        messages.map((message) =>
          message.id === event.messageId
            ? {
                ...message,
                parts: message.parts.map((part) =>
                  part.type === "tool" && part.id === event.toolCallId
                    ? {
                        ...part,
                        approval: {
                          id: part.approval?.id ?? event.toolCallId,
                          state: event.state,
                          ...(part.approval?.reason
                            ? { reason: part.approval.reason }
                            : {}),
                          actor: event.actor,
                          requestedAt: event.requestedAt,
                          resolvedAt: event.resolvedAt,
                          scope: event.scope,
                          executionStatus: event.executionStatus,
                          executionError: event.executionError,
                        },
                      }
                    : part,
                ),
              }
            : message,
        ),
      );
      return;
    }

    if (event.type === "message_state_changed") {
      const terminalTextState =
        event.state === "cancelled" ||
        event.state === "failed" ||
        event.state === "stopped" ||
        event.state === "interrupted"
          ? event.state
          : undefined;
      this.threads.set(
        event.threadId,
        messages.map((message) =>
          message.id === event.messageId
            ? {
                ...message,
                state: event.state,
                parts: terminalTextState
                  ? message.parts.map((part) =>
                      part.type === "text" && part.state === "running"
                        ? { ...part, state: terminalTextState }
                        : part,
                    )
                  : message.parts,
              }
            : message,
        ),
      );
      return;
    }

    this.threads.set(
      event.threadId,
      messages.map((message) =>
        message.id === event.messageId
          ? {
              ...message,
              state: event.state,
              parts: message.parts.map((part) =>
                part.type === "text" && part.state === "running"
                  ? { ...part, state: "complete" as const }
                  : part,
              ),
            }
          : message,
      ),
    );
  }
}
