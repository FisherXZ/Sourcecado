import type { ThreadMessageLike } from "@assistant-ui/react";
import type { ApprovalResource, ToolFailure } from "./protocol";
import type { ProvenanceArtifact, ProvenanceSource } from "./protocol";

export type LegacyStoredMessage = {
  readonly role: string;
  readonly content?: string | null;
  readonly name?: string;
  readonly message_id?: string;
  readonly tool_call_id?: string;
  readonly tool_calls?: readonly {
    readonly id?: string;
    readonly function?: {
      readonly name?: string;
      readonly arguments?: string;
    };
  }[];
};

export type SourcecadoTextPart = {
  readonly type: "text";
  readonly id: string;
  readonly text: string;
  readonly state:
    | "running"
    | "stopping"
    | "complete"
    | "cancelled"
    | "failed"
    | "stopped"
    | "interrupted";
};

export type SourcecadoToolPart = {
  readonly type: "tool";
  readonly id: string;
  readonly name: string;
  readonly arguments: Readonly<Record<string, unknown>>;
  readonly state: "running" | "complete" | "error";
  readonly result?: unknown;
  readonly startedAt?: number;
  readonly completedAt?: number;
  readonly failure?: ToolFailure;
  readonly recovery?: {
    readonly commandId: string;
    readonly action: "retry" | "repair" | "continue";
    readonly status: string;
    readonly outcome?: string;
    readonly repairRoute?: string | null;
  };
  readonly approval?: {
    readonly id: string;
    readonly state:
      | "pending"
      | "submitting"
      | "allowed"
      | "denied"
      | "expired"
      | "cancelled"
      | "failed-submit"
      | "resolved-elsewhere";
    readonly reason?: string;
    readonly actor?: string | null;
    readonly requestedAt?: string;
    readonly resolvedAt?: string;
    readonly scope?: string;
    readonly executionStatus?: string;
    readonly executionError?: string | null;
    readonly resource?: ApprovalResource;
  };
};

export type SourcecadoNoticePart = {
  readonly type: "notice";
  readonly id: string;
  readonly code: string;
  readonly message: string;
  readonly recoverable: boolean;
};

export type SourcecadoSourcePart = ProvenanceSource & {
  readonly type: "source";
};

export type SourcecadoArtifactPart = ProvenanceArtifact & {
  readonly type: "artifact";
};

export type SourcecadoPart =
  | SourcecadoTextPart
  | SourcecadoToolPart
  | SourcecadoNoticePart
  | SourcecadoSourcePart
  | SourcecadoArtifactPart;

export type SourcecadoStructuredMessage = {
  readonly id: string;
  readonly role: "assistant" | "user" | "system";
  readonly state:
    | "running"
    | "stopping"
    | "waiting-approval"
    | "complete"
    | "partial"
    | "cancelled"
    | "failed"
    | "stopped"
    | "interrupted";
  readonly parts: readonly SourcecadoPart[];
};

function parseJson(value: string): unknown {
  try {
    return JSON.parse(value) as unknown;
  } catch {
    return {};
  }
}

export function structureLegacyTranscript(
  threadId: string,
  records: readonly LegacyStoredMessage[],
): SourcecadoStructuredMessage[] {
  const toolResults = new Map<
    string,
    { readonly result: unknown; readonly isError: boolean }
  >();
  for (const record of records) {
    if (record.role !== "tool" || !record.tool_call_id) continue;
    const raw = typeof record.content === "string" ? record.content : "{}";
    const result = parseJson(raw);
    toolResults.set(record.tool_call_id, {
      result,
      isError:
        typeof result === "object" && result !== null && "error" in result,
    });
  }

  return records.flatMap((record, index): SourcecadoStructuredMessage[] => {
    if (record.role !== "user" && record.role !== "assistant") return [];
    const messageId = `${threadId}:legacy:${index}`;
    const parts: SourcecadoPart[] = [];
    if (typeof record.content === "string" && record.content.length > 0) {
      parts.push({
        type: "text",
        id: `${messageId}:text:0`,
        text: record.content,
        state: "complete",
      });
    }
    if (record.role === "assistant") {
      for (const [toolIndex, toolCall] of (record.tool_calls ?? []).entries()) {
        const toolCallId = toolCall.id ?? `${messageId}:tool:${toolIndex}`;
        const settled = toolResults.get(toolCallId);
        parts.push({
          type: "tool",
          id: toolCallId,
          name: toolCall.function?.name ?? "tool",
          arguments: parseJson(toolCall.function?.arguments ?? "{}") as Record<
            string,
            unknown
          >,
          state: settled?.isError
            ? "error"
            : settled
              ? "complete"
              : "running",
          ...(settled ? { result: settled.result } : {}),
        });
      }
    }
    if (parts.length === 0) return [];
    return [
      {
        id: messageId,
        role: record.role,
        state: "complete",
        parts,
      },
    ];
  });
}

function textPartStatus(state: SourcecadoTextPart["state"]) {
  switch (state) {
    case "running":
      return { type: "running" as const };
    case "stopping":
      return { type: "running" as const };
    case "complete":
      return { type: "complete" as const };
    case "cancelled":
      return { type: "incomplete" as const, reason: "cancelled" as const };
    case "failed":
      return { type: "incomplete" as const, reason: "error" as const };
    case "stopped":
      return { type: "incomplete" as const, reason: "cancelled" as const };
    case "interrupted":
      return { type: "incomplete" as const, reason: "other" as const };
  }
}

function messageStatus(state: SourcecadoStructuredMessage["state"]) {
  switch (state) {
    case "running":
      return { type: "running" as const };
    case "waiting-approval":
      return { type: "requires-action" as const, reason: "tool-calls" as const };
    case "complete":
      return { type: "complete" as const, reason: "stop" as const };
    case "partial":
      return { type: "incomplete" as const, reason: "other" as const };
    case "cancelled":
      return { type: "incomplete" as const, reason: "cancelled" as const };
    case "failed":
      return { type: "incomplete" as const, reason: "error" as const };
    case "stopped":
      return { type: "incomplete" as const, reason: "cancelled" as const };
    case "interrupted":
      return { type: "incomplete" as const, reason: "other" as const };
  }
}

export function convertStructuredMessage(
  message: SourcecadoStructuredMessage,
): ThreadMessageLike {
  const content: Exclude<ThreadMessageLike["content"], string>[number][] =
    message.parts.flatMap((part): Exclude<ThreadMessageLike["content"], string>[number][] => {
      if (part.type === "text") {
        return [{
          type: "text" as const,
          text: part.text,
          parentId: part.id,
          status: textPartStatus(part.state),
        }];
      }
      if (part.type === "notice") {
        return [{
          type: "text" as const,
          text: part.message,
          parentId: part.id,
          status: { type: "complete" as const },
        }];
      }
      if (part.type === "source") {
        return [part.url
          ? {
              type: "source" as const,
              sourceType: "url" as const,
              id: part.id,
              url: part.url,
              title: part.title,
              providerMetadata: {
                sourcecado: {
                  provider: part.provider,
                  stale: part.stale,
                  truncated: part.truncated,
                },
              },
            }
          : {
              type: "source" as const,
              sourceType: "document" as const,
              id: part.id,
              title: part.title,
              mediaType: "text/plain",
              providerMetadata: {
                sourcecado: {
                  provider: part.provider,
                  stale: part.stale,
                  truncated: part.truncated,
                },
              },
            }];
      }
      if (part.type === "artifact") return [];
      return [{
        type: "tool-call" as const,
        toolCallId: part.id,
        toolName: part.name,
        args: part.arguments as never,
        argsText: JSON.stringify(part.arguments),
        ...(part.result !== undefined ? { result: part.result } : {}),
        isError: part.state === "error",
        ...(part.startedAt !== undefined
          ? {
              timing: {
                startedAt: part.startedAt,
                ...(part.completedAt !== undefined
                  ? { completedAt: part.completedAt }
                  : {}),
              },
            }
          : {}),
        ...(part.failure || part.recovery || part.approval
          ? {
              providerMetadata: {
                sourcecado: {
                  failure: part.failure ?? null,
                  recovery: part.recovery ?? null,
                  approvalState: part.approval?.state ?? null,
                  actor: part.approval?.actor ?? null,
                  requestedAt: part.approval?.requestedAt ?? null,
                  resolvedAt: part.approval?.resolvedAt ?? null,
                  scope: part.approval?.scope ?? null,
                  executionStatus:
                    part.approval?.executionStatus ?? null,
                  executionError: part.approval?.executionError ?? null,
                  resource: part.approval?.resource ?? null,
                },
              },
            }
          : {}),
        ...(part.approval
          ? {
              approval: {
                id: part.approval.id,
                ...(part.approval.state === "allowed"
                  ? { approved: true }
                  : part.approval.state === "denied"
                    ? { approved: false }
                    : part.approval.state === "cancelled" ||
                        part.approval.state === "expired"
                      ? { resolution: part.approval.state }
                      : {}),
                ...(part.approval.reason
                  ? { reason: part.approval.reason }
                  : {}),
              },
            }
          : {}),
      }];
    });

  return {
    id: message.id,
    role: message.role,
    content,
    ...(message.role === "assistant"
      ? { status: messageStatus(message.state) }
      : {}),
    metadata: {
      custom: {
        sourcecadoState: message.state,
        sourcecadoPartIds: message.parts.map((part) => part.id),
        ...(message.parts.some((part) => part.type === "notice")
          ? { sourcecadoNotice: true }
          : {}),
        ...(message.parts.some((part) => part.type === "artifact")
          ? {
              sourcecadoArtifacts: message.parts
                .filter(
                  (part): part is SourcecadoArtifactPart =>
                    part.type === "artifact",
                )
                .map((part) => ({ ...part })),
            }
          : {}),
      },
    },
  };
}
