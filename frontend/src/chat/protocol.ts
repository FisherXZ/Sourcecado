/**
 * Canonical backend presentation-event contract.
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

export type ProviderFailure = {
  readonly code: string;
  readonly provider: string;
  readonly model: string;
  readonly attempts: number;
  readonly recovery_count: number;
  readonly exhausted: boolean;
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
        readonly run_budget?: RunBudgetStatus;
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
        readonly run_budget?: RunBudgetStatus;
      }
    | {
        readonly type: "error";
        readonly message: string;
        readonly state: "failed";
        /**
         * What kind of failure it was, so the interface can say whether
         * retrying is worth it. Optional: an older backend omits it.
         */
        readonly error_kind?: string;
        readonly failure?: ProviderFailure;
      }
    /**
     * A consequential call was dispatched and never reported back.
     *
     * Not a softer `failed`. Nobody knows whether the mail went out, so the
     * effect is on the operator's review queue and `effect_id` is the row to
     * open. Telling a director a send failed is how a second one gets sent.
     */
    | {
        readonly type: "error";
        readonly message: string;
        readonly state: "held";
        readonly code: "outcome_unknown";
        readonly effect_id: string;
      }
  );

/**
 * What the operator is told about compaction: counts, never content.
 *
 * The backend builds the compacted context for the model, and part of that
 * context is a model-written summary of earlier turns. That summary is one
 * model's account of the session, not Sourcecado's record of it, so it must
 * never reach the thread where the operator would read it as Sourcecado
 * explaining itself. The allowlist below is what keeps it out: fields are
 * copied one at a time, so a backend that starts sending summary text has it
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

function providerFailure(value: unknown): ProviderFailure | undefined {
  if (!isRecord(value)) return undefined;
  if (
    typeof value.code !== "string" ||
    !value.code ||
    typeof value.provider !== "string" ||
    !value.provider ||
    typeof value.model !== "string" ||
    !value.model ||
    typeof value.attempts !== "number" ||
    value.attempts < 1 ||
    typeof value.recovery_count !== "number" ||
    value.recovery_count < 0 ||
    typeof value.exhausted !== "boolean"
  ) {
    return undefined;
  }
  return {
    code: value.code,
    provider: value.provider,
    model: value.model,
    attempts: value.attempts,
    recovery_count: value.recovery_count,
    exhausted: value.exhausted,
  };
}

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

/**
 * What the operator is told about a run's budget: counts and outcomes, never
 * prose from the backend.
 *
 * Same discipline as `CompactionNotice`. Fields are copied one at a time and
 * every sentence below is written here, so a backend that starts sending a
 * reassuring message cannot get it rendered as Sourcecado's own account of
 * what happened.
 */
export type RunBudgetName =
  | "model_turns"
  | "tool_calls"
  | "elapsed_seconds"
  | "input_tokens"
  | "output_tokens"
  | "estimated_cost_usd";

export type RunBudgetMeasurements = Readonly<Record<RunBudgetName, number>>;

export type RunBudgetWarning = {
  readonly budget: RunBudgetName;
  readonly used: number;
  readonly limit: number;
  readonly used_ratio: number;
};

export type RunBudgetReceipt = {
  readonly id: string;
  readonly name: string;
  readonly ok: boolean;
};

export type RunBudgetStatus = {
  readonly state: "running" | "warning" | "exhausted" | "finished";
  /** A budget name, or "loop" when the run was stopped for repeating itself. */
  readonly stopped_by: RunBudgetName | "loop" | null;
  readonly exhausted: readonly RunBudgetName[];
  readonly repeats: number;
  readonly consumed: RunBudgetMeasurements;
  readonly limits: RunBudgetMeasurements;
  readonly warning: RunBudgetWarning | null;
  readonly completed: readonly RunBudgetReceipt[];
  readonly remaining: {
    readonly requested_tools: readonly { readonly id: string; readonly name: string }[];
    /** False whenever the run ended without the model closing it. */
    readonly final_answer: boolean;
  };
  readonly unpriced_requests: number;
  readonly unmeasured_requests: number;
  readonly continue_available: boolean;
};

const RUN_BUDGET_NAMES: readonly RunBudgetName[] = [
  "model_turns",
  "tool_calls",
  "elapsed_seconds",
  "input_tokens",
  "output_tokens",
  "estimated_cost_usd",
];

const RUN_BUDGET_LABELS: Readonly<Record<RunBudgetName, string>> = {
  model_turns: "model turns",
  tool_calls: "tool calls",
  elapsed_seconds: "time",
  input_tokens: "input tokens",
  output_tokens: "output tokens",
  estimated_cost_usd: "estimated cost",
};

/** The text Sourcecado shows the operator when the composer offers to go on. */
export const CONTINUE_RUN_PROMPT = "Continue this run from where it stopped.";

function budgetName(value: unknown): RunBudgetName | null {
  return RUN_BUDGET_NAMES.includes(value as RunBudgetName)
    ? (value as RunBudgetName)
    : null;
}

function measurements(value: unknown): RunBudgetMeasurements {
  const source = isRecord(value) ? value : {};
  const out = {} as Record<RunBudgetName, number>;
  for (const name of RUN_BUDGET_NAMES) out[name] = countOf(source[name]);
  return out;
}

function budgetWarning(value: unknown): RunBudgetWarning | null {
  if (!isRecord(value)) return null;
  const budget = budgetName(value.budget);
  if (!budget) return null;
  return {
    budget,
    used: countOf(value.used),
    limit: countOf(value.limit),
    used_ratio: countOf(value.used_ratio),
  };
}

function budgetReceipts(value: unknown): RunBudgetReceipt[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry) =>
    isRecord(entry) &&
    typeof entry.id === "string" &&
    typeof entry.name === "string"
      ? [{ id: entry.id, name: entry.name, ok: entry.ok === true }]
      : [],
  );
}

export function runBudgetStatus(value: unknown): RunBudgetStatus | undefined {
  if (!isRecord(value)) return undefined;
  const state = ["running", "warning", "exhausted", "finished"].includes(
    String(value.state),
  )
    ? (value.state as RunBudgetStatus["state"])
    : "running";
  const remaining = isRecord(value.remaining) ? value.remaining : {};
  const requested = Array.isArray(remaining.requested_tools)
    ? remaining.requested_tools
    : [];
  return {
    state,
    stopped_by:
      value.stopped_by === "loop" ? "loop" : budgetName(value.stopped_by),
    exhausted: Array.isArray(value.exhausted)
      ? value.exhausted.flatMap((entry) => {
          const name = budgetName(entry);
          return name ? [name] : [];
        })
      : [],
    repeats: countOf(value.repeats),
    consumed: measurements(value.consumed),
    limits: measurements(value.limits),
    warning: budgetWarning(value.warning),
    completed: budgetReceipts(value.completed),
    remaining: {
      requested_tools: requested.flatMap((entry: unknown) =>
        isRecord(entry) &&
        typeof entry.id === "string" &&
        typeof entry.name === "string"
          ? [{ id: entry.id, name: entry.name }]
          : [],
      ),
      // Absent means "we were not told the run closed", which is the safe
      // reading: unfinished until the backend says otherwise.
      final_answer: remaining.final_answer === true,
    },
    unpriced_requests: countOf(value.unpriced_requests),
    unmeasured_requests: countOf(value.unmeasured_requests),
    continue_available: value.continue_available === true,
  };
}

function runBudgetLabel(name: RunBudgetName): string {
  return RUN_BUDGET_LABELS[name];
}

/** One plain sentence, before the stop, naming the budget that is running out. */
export function runBudgetWarningText(warning: RunBudgetWarning): string {
  const percent = Math.min(100, Math.round(warning.used_ratio * 100));
  return (
    `This run has used ${percent}% of its ${runBudgetLabel(warning.budget)} ` +
    "budget. It will stop and ask before going further."
  );
}

/**
 * What a stopped run says about itself. It never reads as a conclusion: the
 * first sentence says the run did not finish, and the rest is what it did.
 */
export function runBudgetStopText(status: RunBudgetStatus): string {
  const parts: string[] = [];
  if (status.stopped_by === "loop") {
    parts.push(
      `Sourcecado stopped this run after ${status.repeats} tool calls in a ` +
        "row that returned nothing new. It was repeating itself, not making " +
        "progress, so it did not finish.",
    );
  } else {
    const names = status.exhausted.length
      ? status.exhausted.map(runBudgetLabel).join(" and ")
      : "run";
    parts.push(
      `Sourcecado stopped this run at its ${names} budget. It did not finish.`,
    );
  }
  const ran = status.completed.length;
  const failed = status.completed.filter((receipt) => !receipt.ok).length;
  parts.push(
    ran === 1
      ? "1 tool step completed."
      : `${ran} tool steps completed.`,
  );
  if (failed > 0) {
    parts.push(`${failed} of them failed.`);
  }
  const queued = status.remaining.requested_tools.length;
  if (queued > 0) {
    parts.push(
      queued === 1
        ? "1 more was queued and never ran."
        : `${queued} more were queued and never ran.`,
    );
  }
  if (!status.remaining.final_answer) {
    parts.push("No final answer was written, so anything above is partial.");
  }
  if (status.unpriced_requests > 0) {
    parts.push(
      `The cost figure does not cover ${status.unpriced_requests} of this ` +
        "run's model requests, because the provider reported no price for them.",
    );
  }
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
 * Client-generated transport status. The backend never sends this; `openChat`
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
 * else the backend might ever attach (a body, a token) can reach the UI.
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
      (value.type === "error" &&
        value.state === "held" &&
        value.code === "outcome_unknown" &&
        typeof value.effect_id === "string" &&
        value.effect_id.length > 0 &&
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
      if (value.type === "error" && value.state === "failed") {
        const sanitized: Record<string, unknown> = { ...value };
        delete sanitized.failure;
        const failure = providerFailure(value.failure);
        if (failure) sanitized.failure = failure;
        return sanitized as ProtocolChatEvent;
      }
      if (
        (value.type === "turn_end" || value.type === "tool_started") &&
        "run_budget" in value
      ) {
        const budget = runBudgetStatus(value.run_budget);
        const sanitized: Record<string, unknown> = { ...value };
        delete sanitized.run_budget;
        if (budget) sanitized.run_budget = budget;
        if (value.type === "turn_end" && "compaction" in sanitized) {
          const notice = compactionNotice(sanitized.compaction);
          delete sanitized.compaction;
          if (notice) sanitized.compaction = notice;
        }
        return sanitized as ProtocolChatEvent;
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
    message: "Malformed event from backend.",
    notice: { code: "malformed_event", recoverable: true },
    ...(sessionId ? { session_id: sessionId } : {}),
  };
}

export function parseSocketEvent(value: unknown): SourcecadoSocketEvent {
  return isQueueSnapshot(value) ? value : parseChatEvent(value);
}
