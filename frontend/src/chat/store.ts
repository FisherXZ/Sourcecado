import {
  compactionNoticeText,
  evidenceReferences,
  parseChatEvent,
  type ChatEvent,
  type ConnectionChangeEvent,
  type ProtocolChatEvent,
  type RecoverableChatNotice,
  type TransportChatError,
} from "./protocol";
import {
  highestCompactionGeneration,
  type SourcecadoPart,
  type SourcecadoStructuredMessage,
} from "./messageAdapter";
import type {
  ApprovalResource,
  EvidenceReference,
  ProvenanceArtifact,
  ProvenanceSource,
  RunBudgetStatus,
  ToolFailure,
} from "./protocol";

function isConnectionChange(
  event: unknown,
): event is ConnectionChangeEvent {
  return (
    typeof event === "object" &&
    event !== null &&
    (event as Record<string, unknown>).type === "connection_change"
  );
}

function isTransportError(event: unknown): event is TransportChatError {
  if (typeof event !== "object" || event === null || "version" in event) {
    return false;
  }
  const candidate = event as Record<string, unknown>;
  return (
    candidate.type === "error" &&
    typeof candidate.message === "string" &&
    candidate.notice === undefined
  );
}

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

/**
 * Marks a message array as a conversation restore and records which event ids
 * the restore snapshot covered. `replaceThread` uses the tag to re-apply live
 * events that arrived while the snapshot was in flight, instead of wiping
 * them.
 */
const restoredSnapshotIds = new WeakMap<
  readonly SourcecadoStructuredMessage[],
  ReadonlySet<string>
>();

export function tagRestoredThread(
  messages: SourcecadoStructuredMessage[],
  snapshotEventIds: ReadonlySet<string>,
): SourcecadoStructuredMessage[] {
  restoredSnapshotIds.set(messages, snapshotEventIds);
  return messages;
}

/**
 * Where the operator sent a message while the thread had no restore snapshot
 * yet. It sits in the live buffer between the events that came before and
 * after it, so `replaceThread` can put the message back in the same order.
 */
type SentBeforeRestore = { readonly sentMessageId: string };
type LiveEntry = ProtocolChatEvent | SentBeforeRestore;
type TurnStart = Extract<ProtocolChatEvent, { type: "turn_start" }>;

function sentBeforeRestore(entry: LiveEntry): entry is SentBeforeRestore {
  return "sentMessageId" in entry;
}

/** The run the message at `from` started: the first turn that began after it. */
function nextTurnStart(
  buffered: readonly LiveEntry[],
  from: number,
): TurnStart | undefined {
  for (const entry of buffered.slice(from)) {
    if (!sentBeforeRestore(entry) && entry.type === "turn_start") return entry;
  }
  return undefined;
}

function textOf(message: SourcecadoStructuredMessage): string {
  return message.parts
    .map((part) => (part.type === "text" ? part.text : ""))
    .join("")
    .trim();
}

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
      readonly type: "reasoning_delta";
      readonly threadId: string;
      readonly messageId: string;
      readonly partId: string;
      readonly delta: string;
      readonly at?: number;
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
      readonly evidence?: readonly EvidenceReference[];
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
      readonly resource?: ApprovalResource;
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
        | "held"
        | "stopped"
        | "interrupted";
    }
  | {
      readonly type: "run_budget_changed";
      readonly threadId: string;
      readonly messageId: string;
      readonly status: RunBudgetStatus;
    }
  | {
      readonly type: "run_compaction_known";
      readonly threadId: string;
      readonly messageId: string;
      readonly generation: number;
    }
  | {
      readonly type: "message_finished";
      readonly threadId: string;
      readonly messageId: string;
      readonly state: "complete";
    };

const LIVE_EVENT_BUFFER_LIMIT = 1000;

/**
 * Reasoning is over once the answer text or a tool call starts, or the turn
 * ends. The thread collapses the thinking row at that point.
 */
function settleReasoning(
  parts: readonly SourcecadoPart[],
): readonly SourcecadoPart[] {
  return parts.some((part) => part.type === "reasoning" && part.state === "running")
    ? parts.map((part) =>
        part.type === "reasoning" && part.state === "running"
          ? { ...part, state: "complete" as const }
          : part,
      )
    : parts;
}

function settleReasoningIn(
  messages: readonly SourcecadoStructuredMessage[],
  messageId: string,
): readonly SourcecadoStructuredMessage[] {
  return messages.map((message) =>
    message.id === messageId
      ? { ...message, parts: settleReasoning(message.parts) }
      : message,
  );
}

export class SourcecadoChatStore {
  private readonly threads = new Map<
    string,
    readonly SourcecadoStructuredMessage[]
  >();
  private readonly seenEventIds = new Set<string>();
  // Raw live events per thread, kept until the thread's restore snapshot
  // lands so `replaceThread` can re-apply the tail the snapshot missed.
  private readonly liveEvents = new Map<string, LiveEntry[]>();
  private readonly hydratedThreads = new Set<string>();
  private noticeNumber = 0;
  private sentNumber = 0;
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
    const snapshotIds = restoredSnapshotIds.get(messages);
    const buffered = this.liveEvents.get(threadId) ?? [];
    const unrestored = this.messagesFor(threadId);
    this.threads.set(threadId, messages);
    if (snapshotIds) {
      for (const id of snapshotIds) this.seenEventIds.add(id);
      for (const [position, event] of buffered.entries()) {
        if (sentBeforeRestore(event)) {
          const sent = unrestored.find(
            (message) => message.id === event.sentMessageId,
          );
          if (sent) {
            const run = nextTurnStart(buffered, position + 1);
            this.restoreSentMessage(threadId, sent, run, snapshotIds);
          }
          continue;
        }
        if (snapshotIds.has(event.event_id)) {
          // The snapshot already projected this event; it still proves the
          // run is alive when it re-announces an interrupted turn.
          this.reviveInterruptedRun(event);
          continue;
        }
        this.applyProtocolEvent(event);
      }
    }
    this.hydratedThreads.add(threadId);
    this.liveEvents.delete(threadId);
  }

  /**
   * The operator sent `sent` before the restore snapshot arrived, so the
   * snapshot overwrote it. Put it back before the answer it started, unless
   * the snapshot already holds it.
   */
  private restoreSentMessage(
    threadId: string,
    sent: SourcecadoStructuredMessage,
    run: TurnStart | undefined,
    snapshotIds: ReadonlySet<string>,
  ): void {
    const current = this.messagesFor(threadId);
    let at = current.length;
    if (run && snapshotIds.has(run.event_id)) {
      // The snapshot saw the run start, and a run stores its operator message
      // just before the answer. An equal message right there is this one.
      const start = current.findIndex((message) => message.id === run.message_id);
      if (start >= 0) {
        const before = current[start - 1];
        if (before?.role === "user" && textOf(before) === textOf(sent)) return;
        at = start;
      }
    }
    // The page numbered this message before it knew how many the thread has,
    // so its id can equal a restored one.
    const id = `${threadId}:sent:${++this.sentNumber}`;
    const kept = {
      ...sent,
      id,
      parts: sent.parts.map((part) =>
        part.id.startsWith(sent.id)
          ? { ...part, id: id + part.id.slice(sent.id.length) }
          : part,
      ),
    };
    this.threads.set(threadId, [
      ...current.slice(0, at),
      kept,
      ...current.slice(at),
    ]);
  }

  applyChatEvent(value: unknown): ChatEvent | ConnectionChangeEvent {
    if (isConnectionChange(value)) return value;
    if (isTransportError(value)) {
      this.addNotice(
        value.session_id ?? this.activeThreadId,
        "transport",
        value.message,
      );
      return value;
    }
    const event = isRecoverableNotice(value) ? value : parseChatEvent(value);
    if (!("version" in event)) {
      if (isRecoverableNotice(event)) this.applyNotice(event);
      return event;
    }
    if (this.seenEventIds.has(event.event_id)) {
      this.reviveInterruptedRun(event);
      return event;
    }
    this.seenEventIds.add(event.event_id);
    this.bufferLive(event.session_id, event);
    this.applyProtocolEvent(event);
    return event;
  }

  private bufferLive(threadId: string, entry: LiveEntry): void {
    if (this.hydratedThreads.has(threadId)) return;
    const buffer = this.liveEvents.get(threadId) ?? [];
    buffer.push(entry);
    if (buffer.length > LIVE_EVENT_BUFFER_LIMIT) buffer.shift();
    this.liveEvents.set(threadId, buffer);
  }

  // A restore snapshot marks a still-open turn "interrupted". A live event
  // for that turn proves the run is in flight, so put it back to running.
  private reviveInterruptedRun(event: ProtocolChatEvent): void {
    if (event.type !== "turn_start") return;
    const messages = this.messagesFor(event.session_id);
    const target = messages.find((message) => message.id === event.message_id);
    if (!target || target.state !== "interrupted") return;
    this.threads.set(
      event.session_id,
      messages.map((message) =>
        message.id === event.message_id
          ? {
              ...message,
              state: "running" as const,
              parts: message.parts.map((part, index) =>
                part.type === "text" && part.state === "interrupted"
                  ? { ...part, state: "running" as const }
                  : // The snapshot settled a block the model was still in.
                    part.type === "reasoning" &&
                      index === message.parts.length - 1
                    ? { ...part, state: "running" as const }
                    : part,
              ),
            }
          : message,
      ),
    );
  }

  private applyNotice(event: RecoverableChatNotice): void {
    this.addNotice(
      event.session_id ?? this.activeThreadId,
      event.notice.code,
      event.message,
    );
  }

  private addNotice(
    threadId: string,
    code: string,
    message: string,
    recoverable = true,
    provider?: { readonly provider: string; readonly model: string },
  ): void {
    let messages = this.messagesFor(threadId);
    let occurrences = 1;
    if (!recoverable) {
      for (let index = messages.length - 1; index >= 0; index -= 1) {
        const candidate = messages[index];
        const notice = candidate?.parts.find((part) => part.type === "notice");
        if (notice?.type === "notice" && !notice.recoverable && notice.code === code) {
          occurrences = (notice.occurrences ?? 1) + 1;
          messages = messages.filter((_message, messageIndex) => messageIndex !== index);
          break;
        }
        if (
          candidate?.role === "assistant" &&
          candidate.state === "complete" &&
          candidate.parts.some((part) => part.type === "text" || part.type === "tool")
        ) {
          break;
        }
      }
    }
    // A restored thread numbered its own receipts from 1, so skip ids in use.
    let messageId: string;
    do {
      messageId = `${threadId}:notice:${++this.noticeNumber}`;
    } while (messages.some((message) => message.id === messageId));
    this.threads.set(threadId, [
      ...messages,
      {
        id: messageId,
        role: "assistant",
        state: "partial",
        parts: [
          {
            type: "notice",
            id: `${messageId}:part`,
            code,
            message:
              occurrences > 1
                ? `${message} ${occurrences} consecutive failed turns had this same classified outcome.`
                : message,
            recoverable,
            ...(occurrences > 1 ? { occurrences } : {}),
            ...provider,
          },
        ],
      },
    ]);
  }

  /**
   * Marks every turn still running on a thread as interrupted. For a run the
   * backend lost: its process ended, so no terminal event will ever arrive.
   */
  interruptRunning(threadId: string): void {
    for (const message of this.messagesFor(threadId)) {
      if (
        message.role === "assistant" &&
        (message.state === "running" || message.state === "stopping")
      ) {
        this.apply({
          type: "message_state_changed",
          threadId,
          messageId: message.id,
          state: "interrupted",
        });
      }
    }
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
    if (event.type === "reasoning_delta") {
      const at = event.at ? Date.parse(event.at) : undefined;
      this.apply({
        type: "reasoning_delta",
        threadId: event.session_id,
        messageId: event.message_id,
        partId: `${event.part_id}:reasoning:${event.step ?? 1}`,
        delta: event.delta,
        ...(at !== undefined && Number.isFinite(at) ? { at } : {}),
      });
      return;
    }
    if (event.type === "provider_recovery") {
      // The attempt that was reasoning has failed. Its block is over, so the
      // live count stops here. The retry opens a new block if it reasons.
      this.threads.set(
        event.session_id,
        settleReasoningIn(this.messagesFor(event.session_id), event.message_id),
      );
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
        ...(event.resource ? { resource: event.resource } : {}),
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
      if (event.run_budget) {
        this.apply({
          type: "run_budget_changed",
          threadId: event.session_id,
          messageId: event.message_id,
          status: event.run_budget,
        });
      }
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
        ...(Array.isArray(event.evidence)
          ? { evidence: evidenceReferences(event.evidence) }
          : {}),
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
      if (event.run_budget) {
        this.apply({
          type: "run_budget_changed",
          threadId: event.session_id,
          messageId: event.message_id,
          status: event.run_budget,
        });
      }
      if (event.compaction) {
        // The backend sends the standing generation on every run while a
        // compaction is in effect. Only a run that raised it compacted.
        const compacted =
          event.compaction.generation >
          highestCompactionGeneration(
            this.messagesFor(event.session_id).filter(
              (message) => message.id !== event.message_id,
            ),
          );
        this.apply({
          type: "run_compaction_known",
          threadId: event.session_id,
          messageId: event.message_id,
          generation: event.compaction.generation,
        });
        if (compacted) {
          this.addNotice(
            event.session_id,
            "compacted",
            compactionNoticeText(event.compaction),
          );
        }
      }
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
        // `held` carries through unchanged. Collapsing it into `failed` here
        // would undo the whole point of the backend sending it.
        state: event.state === "held" ? "held" : "failed",
      });
      if (event.state !== "held") {
        // The backend already sends operator-safe text explaining the
        // failure. Dropping it left a dead turn indistinguishable from a
        // slow one (#136). `held` is excluded: it has its own surface and
        // is not a failure.
        this.addNotice(
          event.session_id,
          event.failure?.code ??
            (typeof event.error_kind === "string" && event.error_kind
            ? event.error_kind
            : "error"),
          event.message,
          false,
          event.failure
            ? { provider: event.failure.provider, model: event.failure.model }
            : undefined,
        );
      }
    }
  }

  apply(event: SourcecadoEvent): void {
    const messages = this.messagesFor(event.threadId);
    if (event.type === "message_started") {
      // Only the page's own send starts a user message. Before the restore
      // snapshot lands, note where it falls among the live events.
      if (event.role === "user") {
        this.bufferLive(event.threadId, { sentMessageId: event.messageId });
      }
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

    if (event.type === "run_budget_changed") {
      this.threads.set(
        event.threadId,
        messages.map((message) =>
          message.id === event.messageId
            ? { ...message, runBudget: event.status }
            : message,
        ),
      );
      return;
    }

    if (event.type === "run_compaction_known") {
      this.threads.set(
        event.threadId,
        messages.map((message) =>
          message.id === event.messageId
            ? { ...message, compactionGeneration: event.generation }
            : message,
        ),
      );
      return;
    }

    if (event.type === "text_delta") {
      this.threads.set(
        event.threadId,
        messages.map((message) => {
          if (message.id !== event.messageId) return message;
          // The backend sends every text delta of a turn under one part id. A
          // step in between splits the text into segments, so text before a
          // step (a lead-in) and text after it (the answer) stay separate and
          // keep their order relative to the steps.
          const segmentIds = message.parts.filter(
            (part) =>
              part.type === "text" &&
              (part.id === event.partId ||
                part.id.startsWith(`${event.partId}~`)),
          );
          const last = message.parts[message.parts.length - 1];
          const current =
            last?.type === "text" &&
            (last.id === event.partId || last.id.startsWith(`${event.partId}~`))
              ? last
              : undefined;
          // A live delta to an "interrupted" restore proves the run is
          // still streaming; put the message and its part back to running.
          const revived = message.state === "interrupted";
          const settled = settleReasoning(message.parts);
          return {
            ...message,
            ...(revived ? { state: "running" as const } : {}),
            parts: current
              ? settled.map((part) =>
                  part === current
                    ? {
                        ...part,
                        text: part.text + event.delta,
                        ...(revived ? { state: "running" as const } : {}),
                      }
                    : part,
                )
              : [
                  ...settled,
                  {
                    type: "text" as const,
                    id:
                      segmentIds.length === 0
                        ? event.partId
                        : `${event.partId}~${segmentIds.length}`,
                    text: event.delta,
                    state: "running" as const,
                  },
                ],
          };
        }),
      );
      return;
    }

    if (event.type === "reasoning_delta") {
      this.threads.set(
        event.threadId,
        messages.map((message) => {
          if (message.id !== event.messageId) return message;
          const known = message.parts.some(
            (part) => part.type === "reasoning" && part.id === event.partId,
          );
          return {
            ...message,
            ...(message.state === "interrupted"
              ? { state: "running" as const }
              : {}),
            parts: known
              ? message.parts.map((part) =>
                  part.type === "reasoning" && part.id === event.partId
                    ? {
                        ...part,
                        text: part.text + event.delta,
                        // A delta means the model is still reasoning. A part
                        // restored from a run that is still going was settled
                        // by the snapshot, so it runs again here.
                        state: "running" as const,
                        ...(event.at !== undefined ? { endedAt: event.at } : {}),
                      }
                    : part,
                )
              : [
                  // A new block means the earlier one is over.
                  ...settleReasoning(message.parts),
                  {
                    type: "reasoning" as const,
                    id: event.partId,
                    text: event.delta,
                    state: "running" as const,
                    ...(event.at !== undefined
                      ? { startedAt: event.at, endedAt: event.at }
                      : {}),
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
        settleReasoningIn(messages, event.messageId).map((message) =>
          message.id === event.messageId
            ? {
                ...message,
                ...(message.state === "interrupted"
                  ? { state: "running" as const }
                  : {}),
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
                        ...(event.evidence ? { evidence: event.evidence } : {}),
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
                          ...(event.resource
                            ? { resource: event.resource }
                            : {}),
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
                          // The receipt names the recipient or person, so the
                          // resolved card keeps what the request resolved.
                          ...(part.approval?.resource
                            ? { resource: part.approval.resource }
                            : {}),
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
        // A held turn is over. Leaving the text part `running` shows a
        // director a spinner for a send nobody knows the outcome of.
        event.state === "held" ||
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
                  ? settleReasoning(message.parts).map((part) =>
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
              parts: settleReasoning(message.parts).map((part) =>
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
