/**
 * Canonical sidecar presentation-event contract.
 *
 * Version 2 keeps the legacy top-level event names and payload fields while
 * adding replay and routing identity shared by live WebSocket and HTTP restore.
 */
export type ChatEventEnvelope = {
  readonly version: 2;
  readonly session_id: string;
  readonly run_id: string;
  readonly event_id: string;
  readonly message_id: string;
  readonly part_id: string;
};

export type ToolFailure = {
  readonly class:
    | "connector_auth"
    | "timeout_network"
    | "permission"
    | "validation"
    | "unknown";
  readonly connector_id: string | null;
  readonly source: string;
  readonly retry_safe: boolean;
  readonly idempotent: boolean;
  readonly summary: string;
  readonly repair_route: string | null;
  readonly detail: string;
  readonly call_id: string;
  readonly run_id: string;
  readonly session_id: string;
  readonly state: "failed";
};

/** Sanitized fields needed to judge an approval; raw bodies/env never cross. */
export type ApprovalResource =
  | {
      readonly kind: "gmail_draft";
      readonly draft_id: string;
      readonly to: string | null;
      readonly subject: string | null;
      readonly account: string | null;
    }
  | {
      readonly kind: "shell_command";
      readonly execution_target: "docker" | "host" | "unknown";
      readonly command_summary: string;
      readonly command_display: string;
      readonly environment_keys: readonly string[];
      readonly cwd: string | null;
      readonly fingerprint: string | null;
      readonly unsandboxed: boolean;
      readonly permanent_eligible: boolean;
    };

export type ProvenanceSource = {
  readonly id: string;
  readonly title: string;
  readonly url: string | null;
  readonly provider: string;
  readonly stale: boolean;
  readonly truncated: boolean;
};

export type ProvenanceArtifact = {
  readonly id: string;
  readonly artifact_type: string;
  readonly title: string;
  readonly preview: string | null;
  readonly external_url: string | null;
  readonly stale: boolean;
  readonly truncated: boolean;
};

export type ProtocolChatEvent = ChatEventEnvelope &
  (
    | { readonly type: "turn_start"; readonly state: "running" }
    | {
        readonly type: "turn_stopping";
        readonly state: "stopping";
        readonly message: string;
      }
    | {
        readonly type: "turn_stopped";
        readonly state: "stopped";
        readonly text: string;
        readonly message: string;
      }
    | { readonly type: "assistant_delta"; readonly delta: string }
    | {
        readonly type: "permission_required";
        readonly id: string;
        readonly name: string;
        readonly arguments: Readonly<Record<string, unknown>>;
        readonly reason: string;
        readonly requested_at?: string;
        readonly scope?: string;
        readonly resource?: ApprovalResource;
      }
    | {
        readonly type: "approval_resolved";
        readonly id: string;
        readonly name: string;
        readonly resolution: "allowed" | "denied" | "cancelled" | "expired";
        readonly decision: "allow" | "deny" | null;
        readonly actor: string | null;
        readonly requested_at: string;
        readonly resolved_at: string;
        readonly scope: string;
        readonly execution_status: string;
        readonly execution_error: string | null;
      }
    | {
        readonly type: "tool_started";
        readonly id: string;
        readonly name: string;
        readonly arguments: Readonly<Record<string, unknown>>;
        readonly started_at?: string;
      }
    | {
        readonly type: "tool_finished";
        readonly id: string;
        readonly name: string;
        readonly ok: boolean;
        readonly result: Readonly<Record<string, unknown>>;
        readonly finished_at?: string;
        readonly failure?: ToolFailure;
        readonly sources?: readonly ProvenanceSource[];
        readonly artifacts?: readonly ProvenanceArtifact[];
      }
    | {
        readonly type: "tool_recovery";
        readonly command_id: string;
        readonly call_id: string;
        readonly name: string;
        readonly action: "retry" | "repair" | "continue";
        readonly status: string;
        readonly outcome?: string;
        readonly repair_route?: string | null;
        readonly failure?: ToolFailure;
        readonly approval_id?: string;
      }
    | {
        readonly type: "provider_recovery";
        readonly action: "retry" | "failover";
        readonly provider: string;
        readonly model: string;
        readonly attempt: number;
        readonly reason: string;
        readonly delay_ms: number;
        readonly message: string;
      }
    | {
        readonly type: "turn_end";
        readonly text: string;
        readonly state: "complete" | "partial" | "stopped" | "interrupted";
        readonly message?: string;
        readonly compaction?: CompactionNotice;
      }
    | {
        readonly type: "error";
        readonly message: string;
        readonly state: "failed";
      }
  );

/**
 * What the operator is told about compaction: counts, never content.
 *
 * The sidecar builds the compacted context for the model, and part of that
 * context is a model-written summary of earlier turns. That summary is one
 * model's account of the session, not Sourcecado's record of it, so it must
 * never reach the thread where the operator would read it as Sourcecado
 * explaining itself. The allowlist below is what keeps it out: fields are
 * copied one at a time, so a sidecar that starts sending summary text has it
 * dropped here rather than rendered.
 */
export type CompactionNotice = {
  readonly generation: number;
  readonly summarized: boolean;
  readonly compacted_messages: number;
  readonly retained_director_messages: number;
  readonly omitted_director_messages: number;
  readonly measurement: string | null;
  readonly rejected_summaries: number;
};

function countOf(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? value
    : 0;
}

export function compactionNotice(value: unknown): CompactionNotice | undefined {
  if (!isRecord(value)) return undefined;
  return {
    generation: countOf(value.generation),
    summarized: value.summarized === true,
    compacted_messages: countOf(value.compacted_messages),
    retained_director_messages: countOf(value.retained_director_messages),
    omitted_director_messages: countOf(value.omitted_director_messages),
    measurement:
      typeof value.measurement === "string" ? value.measurement : null,
    rejected_summaries: countOf(value.rejected_summaries),
  };
}

/** One plain sentence. Says what happened to the conversation, not what the
 * model thinks happened in it. */
export function compactionNoticeText(value: unknown): string {
  const notice = compactionNotice(value);
  if (!notice) return "";
  const parts = [
    `Older parts of this conversation were compacted to fit the model's ` +
      `context. The ${notice.compacted_messages} earliest messages are no ` +
      `longer sent to the model.`,
  ];
  if (!notice.summarized) {
    parts.push(
      "They were dropped mechanically and no summary of them was available.",
    );
  }
  if (notice.omitted_director_messages > 0) {
    parts.push(
      `${notice.omitted_director_messages} of your earlier messages are no ` +
        `longer quoted in full.`,
    );
  }
  parts.push("The full conversation is still saved on this machine.");
  return parts.join(" ");
}

export type RecoverableChatNotice = {
  readonly type: "error";
  readonly message: string;
  readonly notice: {
    readonly code: "malformed_event" | "unsupported_version";
    readonly recoverable: true;
  };
  readonly session_id?: string;
};

export type TransportChatError = {
  readonly type: "error";
  readonly message: string;
  readonly notice?: undefined;
  readonly session_id?: string;
};

export type ChatEvent =
  | ProtocolChatEvent
  | RecoverableChatNotice
  | TransportChatError;

export type SourcecadoQueueItem = {
  readonly id: string;
  readonly session_id: string;
  readonly text: string;
  readonly position: number;
  readonly state:
    | "waiting"
    | "sending"
    | "failed"
    | "interrupted"
    | "offline"
    | "reconnecting";
  readonly error: string | null;
  readonly created_at: string;
  readonly updated_at: string;
};

export type QueueSnapshotEvent = {
  readonly version: 2;
  readonly type: "queue_snapshot";
  readonly session_id: string;
  readonly command_id: string;
  readonly status: string;
  readonly paused: boolean;
  readonly items: readonly SourcecadoQueueItem[];
};

export type ConnectionStatus = "connected" | "reconnecting" | "offline";

/**
 * Client-generated transport status. The sidecar never sends this; `openChat`
 * synthesizes it so consumers can render connection state.
 */
export type ConnectionChangeEvent = {
  readonly type: "connection_change";
  readonly status: ConnectionStatus;
  readonly attempt: number;
  readonly reason: string;
};

export type SourcecadoSocketEvent =
  | ChatEvent
  | QueueSnapshotEvent
  | ConnectionChangeEvent;

export type QueueCommand =
  | {
      readonly type: "queue_add";
      readonly session_id: string;
      readonly command_id: string;
      readonly item_id: string;
      readonly text: string;
    }
  | {
      readonly type: "queue_edit";
      readonly session_id: string;
      readonly command_id: string;
      readonly item_id: string;
      readonly text: string;
    }
  | {
      readonly type: "queue_move";
      readonly session_id: string;
      readonly command_id: string;
      readonly item_id: string;
      readonly before_id?: string;
      readonly after_id?: string;
    }
  | {
      readonly type: "queue_remove" | "queue_retry";
      readonly session_id: string;
      readonly command_id: string;
      readonly item_id: string;
    }
  | {
      readonly type: "queue_resume";
      readonly session_id: string;
      readonly command_id: string;
    };

export type RecoveryCommand = {
  readonly type:
    | "retry_failed_step"
    | "repair_connection"
    | "continue_without_source";
  readonly session_id: string;
  readonly run_id: string;
  readonly call_id: string;
  readonly command_id: string;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

const QUEUE_STATES = new Set([
  "waiting",
  "sending",
  "failed",
  "interrupted",
  "offline",
  "reconnecting",
]);

function isQueueItem(value: unknown): value is SourcecadoQueueItem {
  if (!isRecord(value)) return false;
  return (
    typeof value.id === "string" &&
    typeof value.session_id === "string" &&
    typeof value.text === "string" &&
    typeof value.position === "number" &&
    QUEUE_STATES.has(String(value.state)) &&
    (value.error === null || typeof value.error === "string") &&
    typeof value.created_at === "string" &&
    typeof value.updated_at === "string"
  );
}

function isQueueSnapshot(value: unknown): value is QueueSnapshotEvent {
  return (
    isRecord(value) &&
    value.version === 2 &&
    value.type === "queue_snapshot" &&
    typeof value.session_id === "string" &&
    typeof value.command_id === "string" &&
    typeof value.status === "string" &&
    typeof value.paused === "boolean" &&
    Array.isArray(value.items) &&
    value.items.every(isQueueItem)
  );
}

/**
 * Rebuilds an approval resource from exactly the contract keys, so nothing
 * else the sidecar might ever attach (a body, a token) can reach the UI.
 * Returns undefined when the value is not a usable resource.
 */
function approvalResource(value: unknown): ApprovalResource | undefined {
  if (!isRecord(value)) return undefined;
  if (value.kind === "shell_command") {
    if (
      !["docker", "host", "unknown"].includes(String(value.execution_target)) ||
      typeof value.command_summary !== "string" ||
      typeof value.command_display !== "string" ||
      !Array.isArray(value.environment_keys) ||
      !value.environment_keys.every((key) => typeof key === "string") ||
      (value.cwd !== null && typeof value.cwd !== "string") ||
      (value.fingerprint !== null && typeof value.fingerprint !== "string") ||
      typeof value.unsandboxed !== "boolean" ||
      typeof value.permanent_eligible !== "boolean"
    ) {
      return undefined;
    }
    return {
      kind: "shell_command",
      execution_target: value.execution_target as "docker" | "host" | "unknown",
      command_summary: value.command_summary,
      command_display: value.command_display,
      environment_keys: value.environment_keys as string[],
      cwd: typeof value.cwd === "string" ? value.cwd : null,
      fingerprint: typeof value.fingerprint === "string" ? value.fingerprint : null,
      unsandboxed: value.unsandboxed,
      permanent_eligible: value.permanent_eligible,
    };
  }
  if (value.kind !== "gmail_draft" || typeof value.draft_id !== "string") {
    return undefined;
  }
  return {
    kind: "gmail_draft",
    draft_id: value.draft_id,
    to: typeof value.to === "string" ? value.to : null,
    subject: typeof value.subject === "string" ? value.subject : null,
    account: typeof value.account === "string" ? value.account : null,
  };
}

function hasEnvelope(value: Record<string, unknown>): boolean {
  return (
    value.version === 2 &&
    [
      "type",
      "session_id",
      "run_id",
      "event_id",
      "message_id",
      "part_id",
    ].every((key) => typeof value[key] === "string" && value[key].length > 0)
  );
}

export function parseChatEvent(value: unknown): ChatEvent {
  if (isRecord(value) && hasEnvelope(value)) {
    const valid =
      (value.type === "assistant_delta" && typeof value.delta === "string") ||
      (value.type === "turn_start" && value.state === "running") ||
      (value.type === "turn_stopping" &&
        value.state === "stopping" &&
        typeof value.message === "string") ||
      (value.type === "turn_stopped" &&
        value.state === "stopped" &&
        typeof value.text === "string" &&
        typeof value.message === "string") ||
      (value.type === "turn_end" &&
        typeof value.text === "string" &&
        ["complete", "partial", "stopped", "interrupted"].includes(
          String(value.state),
        )) ||
      (value.type === "error" &&
        value.state === "failed" &&
        typeof value.message === "string") ||
      (value.type === "permission_required" &&
        typeof value.id === "string" &&
        typeof value.name === "string" &&
        isRecord(value.arguments) &&
        typeof value.reason === "string") ||
      (value.type === "approval_resolved" &&
        typeof value.id === "string" &&
        typeof value.name === "string" &&
        ["allowed", "denied", "cancelled", "expired"].includes(
          String(value.resolution),
        ) &&
        (value.decision === "allow" ||
          value.decision === "deny" ||
          value.decision === null) &&
        (typeof value.actor === "string" || value.actor === null) &&
        typeof value.requested_at === "string" &&
        typeof value.resolved_at === "string" &&
        typeof value.scope === "string" &&
        typeof value.execution_status === "string" &&
        (typeof value.execution_error === "string" ||
          value.execution_error === null)) ||
      (value.type === "tool_started" &&
        typeof value.id === "string" &&
        typeof value.name === "string" &&
        isRecord(value.arguments)) ||
      (value.type === "tool_finished" &&
        typeof value.id === "string" &&
        typeof value.name === "string" &&
        typeof value.ok === "boolean" &&
        isRecord(value.result)) ||
      (value.type === "tool_recovery" &&
        typeof value.command_id === "string" &&
        typeof value.call_id === "string" &&
        typeof value.name === "string" &&
        ["retry", "repair", "continue"].includes(String(value.action)) &&
        typeof value.status === "string") ||
      (value.type === "provider_recovery" &&
        ["retry", "failover"].includes(String(value.action)) &&
        typeof value.provider === "string" &&
        typeof value.model === "string" &&
        typeof value.attempt === "number" &&
        value.attempt >= 1 &&
        typeof value.reason === "string" &&
        typeof value.delay_ms === "number" &&
        value.delay_ms >= 0 &&
        typeof value.message === "string");
    if (valid) {
      if (value.type === "provider_recovery") {
        return {
          version: 2,
          type: "provider_recovery",
          session_id: value.session_id as string,
          run_id: value.run_id as string,
          event_id: value.event_id as string,
          message_id: value.message_id as string,
          part_id: value.part_id as string,
          action: value.action as "retry" | "failover",
          provider: value.provider as string,
          model: value.model as string,
          attempt: value.attempt as number,
          reason: value.reason as string,
          delay_ms: value.delay_ms as number,
          message: value.message as string,
        };
      }
      if (value.type === "turn_end" && "compaction" in value) {
        const notice = compactionNotice(value.compaction);
        const sanitized: Record<string, unknown> = { ...value };
        delete sanitized.compaction;
        if (notice) sanitized.compaction = notice;
        return sanitized as ProtocolChatEvent;
      }
      if (value.type === "permission_required" && "resource" in value) {
        const resource = approvalResource(value.resource);
        const sanitized: Record<string, unknown> = { ...value };
        delete sanitized.resource;
        if (resource) sanitized.resource = resource;
        return sanitized as ProtocolChatEvent;
      }
      return value as ProtocolChatEvent;
    }
  }
  const sessionId =
    isRecord(value) && typeof value.session_id === "string"
      ? value.session_id
      : undefined;
  if (isRecord(value) && "version" in value && value.version !== 2) {
    return {
      type: "error",
      message: `Unsupported chat event version ${String(value.version)}.`,
      notice: { code: "unsupported_version", recoverable: true },
      ...(sessionId ? { session_id: sessionId } : {}),
    };
  }
  return {
    type: "error",
    message: "Malformed event from sidecar.",
    notice: { code: "malformed_event", recoverable: true },
    ...(sessionId ? { session_id: sessionId } : {}),
  };
}

export function parseSocketEvent(value: unknown): SourcecadoSocketEvent {
  return isQueueSnapshot(value) ? value : parseChatEvent(value);
}
