import {
  parseChatEvent,
  parseSocketEvent,
  type ChatEvent,
  type ChatEventEnvelope,
  type ConnectionChangeEvent,
  type ConnectionStatus,
  type ProtocolChatEvent,
  type QueueSnapshotEvent,
  type RecoverableChatNotice,
  type QueueCommand,
  type RecoveryCommand,
  type SourcecadoQueueItem,
  type SourcecadoSocketEvent,
} from "./chat/protocol";

export { parseChatEvent };
export type {
  ChatEvent,
  ChatEventEnvelope,
  ConnectionChangeEvent,
  ConnectionStatus,
  ProtocolChatEvent,
  RecoverableChatNotice,
  QueueCommand,
  RecoveryCommand,
  SourcecadoSocketEvent,
};

declare const __CLUB_DEV_TOKEN__: string;

export type Health = {
  status: string;
  piece: string;
  slice: number;
  model: string | null;
  persona?: string;
};

export type PersonaInfo = { id: string; name: string; tools: string[] };

export type ScheduleRunStatus =
  | "running"
  | "success"
  | "failed"
  | "waiting_approval"
  | "partial"
  | "interrupted"
  // Client-only: a status this build does not recognize (a future status,
  // corrupted data). Never written by the sidecar, so it stays out of
  // SCHEDULE_RUN_STATUSES below -- it is not part of the shared contract.
  | "unknown";

export type ScheduleJob = {
  id: number;
  name: string;
  templateId: string;
  cadence: string;
  cron: string;
  prompt: string;
  createdAt: string;
  nextRunAt: string | null;
};

export type ScheduleArtifact = {
  id: string;
  artifactType: string;
  title: string;
  externalUrl: string | null;
};

export type ScheduleRun = {
  id: number;
  jobId: number;
  status: ScheduleRunStatus;
  result: string;
  summary: string;
  createdAt: string;
  startedAt: string;
  finishedAt: string | null;
  durationMs: number;
  sessionId: string;
  waitingApprovalCount: number;
  artifacts: ScheduleArtifact[];
};

export type ScheduleTemplate = {
  id: string;
  name: string;
  description: string;
  cadences: string[];
  defaultPrompt: string;
};

export type Schedule = {
  jobs: ScheduleJob[];
  runs: ScheduleRun[];
  templates: ScheduleTemplate[];
};

export type Hello = { message: string; piece: string; window: string };

export type StoredMessage = {
  role: string;
  content?: string | null;
  name?: string;
  message_id?: string;
  tool_call_id?: string;
  tool_calls?: Array<{
    id?: string;
    function?: { name?: string; arguments?: string };
  }>;
};

export type Conversation = {
  id: string;
  title: string | null;
  messages: StoredMessage[];
  events: ChatEvent[];
  active_person?: ActivePerson | null;
  queue?: QueueItem[];
  queue_paused?: boolean;
};

export type CurrentRunMetrics = {
  run_id: string;
  status: "running" | "success" | "failed" | "cancelled" | "partial";
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  cache_hit_input_tokens: number | null;
  cache_miss_input_tokens: number | null;
  cache_write_input_tokens: number | null;
  reasoning_tokens: number | null;
  current_context_tokens: number | null;
  context_window_tokens: number | null;
  context_use_ratio: number | null;
  elapsed_ms: number | null;
  estimated_cost_usd: number | null;
  retry_count: number;
  compaction_count: number;
};

export type CurrentRunTelemetry = {
  version: 1;
  session_id: string;
  current_run: CurrentRunMetrics | null;
};

export type QueueItem = SourcecadoQueueItem;

const httpBase = (): string =>
  window.__CLUB_HTTP__ ||
  (import.meta as ImportMeta & { env?: { VITE_CLUB_HTTP?: string } }).env?.VITE_CLUB_HTTP ||
  "";

const apiToken = (): string =>
  window.__CLUB_API_TOKEN__ ||
  (import.meta as ImportMeta & { env?: { VITE_CLUB_TOKEN?: string } }).env?.VITE_CLUB_TOKEN ||
  (typeof __CLUB_DEV_TOKEN__ === "string" ? __CLUB_DEV_TOKEN__ : "");

function wsBase(): string {
  const http = httpBase();
  if (!http) {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    return `${proto}://${location.host}`;
  }
  return http.replace(/^http/, "ws");
}

async function get(path: string): Promise<Response> {
  const headers = new Headers();
  const token = apiToken();
  if (token) headers.set("X-Club-Token", token);
  return fetch(`${httpBase()}${path}`, { headers });
}

export async function getHealth(): Promise<Health> {
  const res = await get("/v1/health");
  if (!res.ok) throw new Error(`health ${res.status}`);
  return res.json();
}

export async function getHello(): Promise<Hello> {
  const res = await get("/v1/hello");
  if (!res.ok) throw new Error(`hello ${res.status}`);
  return res.json();
}

export type SessionRow = {
  session_id: string;
  title: string | null;
  n_msgs: number;
  pinned: boolean;
  opened_at: string | null;
  updated_at: string;
};

export type SessionListing = {
  sessions: SessionRow[];
  open_id: string | null;
  last_destination?: string | null;
};

export async function getSessions(): Promise<SessionListing> {
  const res = await get("/v1/sessions");
  if (!res.ok) throw new Error(`sessions ${res.status}`);
  return res.json();
}

export async function setLastDestination(destination: string): Promise<{ destination: string }> {
  const res = await fetch(`${httpBase()}/v1/navigation`, {
    method: "PATCH",
    headers: { "X-Club-Token": apiToken(), "Content-Type": "application/json" },
    body: JSON.stringify({ destination }),
  });
  if (!res.ok) throw new Error(`navigation ${res.status}`);
  return res.json();
}

export async function createSession(): Promise<{ id: string; title: string | null; n_msgs: number }> {
  const res = await fetch(`${httpBase()}/v1/sessions`, {
    method: "POST",
    headers: { "X-Club-Token": apiToken() },
  });
  if (!res.ok) throw new Error(`sessions ${res.status}`);
  return res.json();
}

export async function getSession(
  id: string,
  expectedPersonId?: string,
): Promise<Conversation> {
  const expected = expectedPersonId
    ? `?expected_person_id=${encodeURIComponent(expectedPersonId)}`
    : "";
  const res = await get(`/v1/sessions/${encodeURIComponent(id)}${expected}`);
  if (!res.ok) throw new Error(`session ${res.status}`);
  const body = await res.json();
  const activePerson = body.active_person as Record<string, unknown> | null | undefined;
  if (
    activePerson !== null &&
    activePerson !== undefined &&
    (typeof activePerson.person_id !== "string" ||
      !activePerson.person_id ||
      typeof activePerson.version !== "number" ||
      !Number.isInteger(activePerson.version) ||
      activePerson.version < 1 ||
      typeof activePerson.label !== "string" ||
      !activePerson.label)
  ) {
    throw new Error("session person binding malformed");
  }
  return {
    id: body.id,
    title: body.title,
    messages: body.messages,
    events: Array.isArray(body.events) ? body.events.map(parseChatEvent) : [],
    ...(activePerson
      ? {
          active_person: {
            person_id: activePerson.person_id as string,
            version: activePerson.version as number,
            label: activePerson.label as string,
          },
        }
      : {}),
    ...(Array.isArray(body.queue) ? { queue: body.queue } : {}),
    ...(typeof body.queue_paused === "boolean"
      ? { queue_paused: body.queue_paused }
      : {}),
  };
}

const RUN_METRIC_STATUSES = new Set([
  "running",
  "success",
  "failed",
  "cancelled",
  "partial",
]);

function measurement(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? value
    : null;
}

export async function getCurrentRunTelemetry(
  sessionId: string,
): Promise<CurrentRunTelemetry> {
  const res = await get(
    `/v1/sessions/${encodeURIComponent(sessionId)}/telemetry/current`,
  );
  if (!res.ok) throw new Error(`current-run telemetry ${res.status}`);
  const raw = (await res.json()) as Record<string, unknown>;
  const candidate =
    typeof raw.current_run === "object" &&
    raw.current_run !== null &&
    !Array.isArray(raw.current_run)
      ? (raw.current_run as Record<string, unknown>)
      : null;
  if (raw.version !== 1 || raw.session_id !== sessionId) {
    throw new Error("current-run telemetry payload malformed");
  }
  if (candidate === null) {
    return { version: 1, session_id: sessionId, current_run: null };
  }
  const status = String(candidate.status);
  if (
    typeof candidate.run_id !== "string" ||
    !candidate.run_id ||
    !RUN_METRIC_STATUSES.has(status)
  ) {
    throw new Error("current-run telemetry payload malformed");
  }
  return {
    version: 1,
    session_id: sessionId,
    current_run: {
      run_id: candidate.run_id,
      status: status as CurrentRunMetrics["status"],
      input_tokens: measurement(candidate.input_tokens),
      output_tokens: measurement(candidate.output_tokens),
      total_tokens: measurement(candidate.total_tokens),
      cache_hit_input_tokens: measurement(candidate.cache_hit_input_tokens),
      cache_miss_input_tokens: measurement(candidate.cache_miss_input_tokens),
      cache_write_input_tokens: measurement(candidate.cache_write_input_tokens),
      reasoning_tokens: measurement(candidate.reasoning_tokens),
      current_context_tokens: measurement(candidate.current_context_tokens),
      context_window_tokens: measurement(candidate.context_window_tokens),
      context_use_ratio: measurement(candidate.context_use_ratio),
      elapsed_ms: measurement(candidate.elapsed_ms),
      estimated_cost_usd: measurement(candidate.estimated_cost_usd),
      retry_count: measurement(candidate.retry_count) ?? 0,
      compaction_count: measurement(candidate.compaction_count) ?? 0,
    },
  };
}

export async function renameSession(id: string, title: string): Promise<{ id: string; title: string }> {
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "X-Club-Token": apiToken(), "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  if (!res.ok) throw new Error(`rename ${res.status}`);
  return res.json();
}

export type FollowUpReason = "reply_unanswered" | "reply_needs_review";

export type FollowUp = {
  needed: boolean;
  reason: FollowUpReason | null;
};

export type BoardPerson = {
  person_id: string;
  first_name: string | null;
  last_name: string | null;
  last_name_status?: "known" | "hidden_by_apollo" | "missing";
  title: string | null;
  company: string | null;
  sequence_state: string | null;
  board_lane?: "backlog" | "open" | "in_conversation" | "done";
  last_contact_at?: string | null;
  last_contact_direction?: "outbound" | "inbound" | null;
  replied?: boolean;
  replied_at?: string | null;
  follow_up?: FollowUp;
};

export type Board = {
  backlog: BoardPerson[];
  open: BoardPerson[];
  in_conversation: BoardPerson[];
  done: BoardPerson[];
};

export type PersonSourceRef = {
  id: string;
  person_id: string;
  type: "source_ref";
  restricted: boolean;
  created_at?: string;
  updated_at?: string;
  fields?: Record<string, unknown>;
};

export type PersonKnowledgeGap = {
  id: string;
  person_id: string;
  type: "knowledge_gap";
  restricted: boolean;
  fields?: Record<string, unknown>;
};

export type ReplyRefreshResult = {
  status: "ok" | "failed";
  mode: string;
  scanned: number;
  filed: number;
  unassigned: number;
  cursor?: string | null;
  error?: string;
};

/** One claim in the living brief, with the sources that back it. */
export type BriefClaim = {
  id: string;
  text: string;
  state: "current" | "stale" | "conflicting" | "missing";
  authority: string;
  updated_at: string;
  truncated: boolean;
  source_refs: string[];
};

/** One source reference, and how far the record supports reading it. */
export type BriefSourceRef = {
  id: string;
  provider: string;
  locator: string | null;
  title: string | null;
  observed_at: string;
  modified_at: string | null;
  fresh: boolean;
  evidence: "present" | "absent" | "partial" | "missing" | "ambiguous" | "unsupported" | "expired";
  truncated: boolean;
};

export type BriefHandoff = {
  who: string;
  wanted: string;
  happened: string;
  they_want: string;
  generated: boolean;
  source_refs: string[];
  version: number | null;
  saved_at: string | null;
  stale: boolean;
  stale_fields: Array<"who" | "wanted" | "happened" | "they_want">;
  truncated_fields: Array<"who" | "wanted" | "happened" | "they_want">;
  freshness_unknown: boolean;
};

export type LivingBrief = {
  version: string;
  who: string;
  why: string;
  learned: string[];
  missing: string[];
  sources: string[];
  identity: BriefClaim;
  target: BriefClaim | null;
  state: { sequence: string | null; claim: BriefClaim | null };
  outcome: BriefClaim | null;
  last_contact: {
    at: string | null;
    direction: string | null;
    replied: boolean;
    follow_up: { needed: boolean; reason: string | null };
    claim: BriefClaim | null;
  };
  wants: BriefClaim;
  evidence: BriefClaim[];
  conflicts: BriefClaim[];
  gaps: BriefClaim[];
  artifacts: BriefClaim[];
  claims: BriefClaim[];
  source_refs: BriefSourceRef[];
  restricted_source_count: number;
  partial: boolean;
  partial_sources: string[];
  omitted: number;
  handoff: BriefHandoff;
  person_version: number;
};

export type PersonFile = {
  person: BoardPerson &
    Record<string, unknown> & {
      sources?: PersonSourceRef[];
      knowledge_gaps?: PersonKnowledgeGap[];
    };
  brief: LivingBrief;
  timeline: Array<{
    event_id: string;
    source: string;
    kind: string;
    summary: string;
    payload: Record<string, unknown>;
  }>;
  versions?: Array<{ version: number; created_at: string }>;
  sourcing_chat: { session_id: string; person_id: string } | null;
  meeting_evidence?: MeetingEvidenceView;
};

export type MeetingEvidence = {
  evidence_id: string;
  provider: "calendar" | "granola";
  provider_id: string;
  title: string;
  starts_at: string | null;
  ends_at: string | null;
  participants: Array<{ name: string | null; email: string | null }>;
  source_ref: {
    id: string;
    title: string;
    url: string | null;
    provider: string;
  };
  notes: string | null;
  status: "attached" | "proposed" | "rejected" | "unmatched";
  match_reason: string | null;
};

export type MeetingEvidenceView = {
  attached: MeetingEvidence[];
  proposed: MeetingEvidence[];
  rejected: MeetingEvidence[];
};

export type MeetingRefreshResult = {
  sources: Record<string, { status: "ok"; records: number } | { status: "failed"; error: string }>;
  meeting_evidence?: MeetingEvidenceView;
};

export type DriveEvidenceCandidate = {
  id: string;
  name: string | null;
  mimeType: string | null;
  modifiedTime: string | null;
  parents: string[];
  webViewLink: string | null;
  status: string;
};

export type DriveEvidenceKind = "search_result" | "folder_child" | "read_source";

export type ActivePerson = {
  person_id: string;
  version: number;
  label: string;
};

export type PersonSourcingChatResult = {
  created: boolean;
  session: { id: string; title: string | null; n_msgs: number };
  active_person: ActivePerson;
};

export type ApolloCurationKept = {
  row_index: number;
  apollo_id: string;
  person_id: string;
  version: number;
  operation: "created" | "updated";
  first_name: string | null;
  last_name: string | null;
  last_name_status: "known" | "hidden_by_apollo" | "missing";
  title: string | null;
  company: string | null;
  sourcing_chat: { session_id: string } | null;
};

export type ApolloCurationResult = {
  status: "success" | "partial" | "failed";
  selected_row_count: number;
  selected_identity_count: number;
  kept: ApolloCurationKept[];
  failed: Array<{ row_index: number; apollo_id: string | null; code: string }>;
  duplicates: Array<{ row_index: number; apollo_id: string }>;
  original_session: {
    session_id: string;
    bound_person_id: string | null;
    reason: string;
  };
};

export async function getBoard(): Promise<Board> {
  const res = await get("/v1/board");
  if (!res.ok) throw new Error(`board ${res.status}`);
  return res.json();
}

export async function refreshReplies(): Promise<{
  refresh: ReplyRefreshResult;
  board: Board;
}> {
  const res = await fetch(`${httpBase()}/v1/replies/refresh`, {
    method: "POST",
    headers: { "X-Club-Token": apiToken() },
  });
  if (!res.ok) throw new Error(`reply refresh ${res.status}`);
  return res.json();
}

export async function getPerson(id: string): Promise<PersonFile> {
  const res = await get(`/v1/people/${encodeURIComponent(id)}`);
  if (!res.ok) throw new Error(`person ${res.status}`);
  return res.json();
}

export async function refreshPersonMeetings(id: string): Promise<MeetingRefreshResult> {
  const res = await fetch(
    `${httpBase()}/v1/people/${encodeURIComponent(id)}/meetings/refresh`,
    { method: "POST", headers: { "X-Club-Token": apiToken() } },
  );
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || `meeting refresh ${res.status}`);
  return body;
}

async function reviewPersonMeeting(
  id: string,
  evidenceId: string,
  action: "attach" | "reject",
): Promise<{ meeting: MeetingEvidence; meeting_evidence: MeetingEvidenceView }> {
  const res = await fetch(
    `${httpBase()}/v1/people/${encodeURIComponent(id)}/meetings/${encodeURIComponent(evidenceId)}/${action}`,
    { method: "POST", headers: { "X-Club-Token": apiToken() } },
  );
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || `meeting ${action} ${res.status}`);
  return body;
}

export function attachPersonMeeting(id: string, evidenceId: string) {
  return reviewPersonMeeting(id, evidenceId, "attach");
}

export function rejectPersonMeeting(id: string, evidenceId: string) {
  return reviewPersonMeeting(id, evidenceId, "reject");
}

export async function searchPersonDriveEvidence(
  id: string,
  query: string,
): Promise<{ files: DriveEvidenceCandidate[] }> {
  const res = await fetch(
    `${httpBase()}/v1/people/${encodeURIComponent(id)}/drive-evidence/search`,
    {
      method: "POST",
      headers: { "X-Club-Token": apiToken(), "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    },
  );
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || `drive evidence search ${res.status}`);
  return body;
}

export async function attachPersonDriveEvidence(
  id: string,
  params: { kind: DriveEvidenceKind; fileId: string; folderId?: string },
): Promise<{ source: PersonSourceRef; person: PersonFile["person"] }> {
  const res = await fetch(
    `${httpBase()}/v1/people/${encodeURIComponent(id)}/drive-evidence/attach`,
    {
      method: "POST",
      headers: { "X-Club-Token": apiToken(), "Content-Type": "application/json" },
      body: JSON.stringify({
        kind: params.kind,
        file_id: params.fileId,
        folder_id: params.folderId,
      }),
    },
  );
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || `drive evidence attach ${res.status}`);
  return body;
}

export async function openPersonSourcingChat(
  id: string,
  expectedPersonVersion: number,
): Promise<PersonSourcingChatResult> {
  const res = await fetch(
    `${httpBase()}/v1/people/${encodeURIComponent(id)}/sourcing-chat`,
    {
      method: "POST",
      headers: {
        "X-Club-Token": apiToken(),
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ expected_person_version: expectedPersonVersion }),
    },
  );
  const raw = (await res.json()) as Record<string, unknown>;
  if (!res.ok) throw new Error(`person sourcing chat ${res.status}`);
  const session = raw.session as Record<string, unknown> | undefined;
  const activePerson = raw.active_person as Record<string, unknown> | undefined;
  if (
    typeof raw.created !== "boolean" ||
    !session ||
    typeof session.id !== "string" ||
    !session.id ||
    (session.title !== null && typeof session.title !== "string") ||
    typeof session.n_msgs !== "number" ||
    !Number.isInteger(session.n_msgs) ||
    session.n_msgs < 0 ||
    !activePerson ||
    typeof activePerson.person_id !== "string" ||
    !activePerson.person_id ||
    typeof activePerson.version !== "number" ||
    !Number.isInteger(activePerson.version) ||
    activePerson.version < 1 ||
    typeof activePerson.label !== "string" ||
    !activePerson.label
  ) {
    throw new Error("person sourcing chat payload malformed");
  }
  return {
    created: raw.created,
    session: {
      id: session.id,
      title: session.title as string | null,
      n_msgs: session.n_msgs,
    },
    active_person: {
      person_id: activePerson.person_id,
      version: activePerson.version,
      label: activePerson.label,
    },
  };
}

export async function curateApolloCandidates(input: {
  sessionId: string;
  target: string;
  people: Array<Record<string, unknown>>;
  bindOriginal: boolean;
}): Promise<ApolloCurationResult> {
  const res = await fetch(`${httpBase()}/v1/apollo/curate`, {
    method: "POST",
    headers: {
      "X-Club-Token": apiToken(),
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      session_id: input.sessionId,
      target: input.target,
      people: input.people,
      bind_original: input.bindOriginal,
    }),
  });
  const raw = (await res.json()) as Record<string, unknown>;
  if (!res.ok) throw new Error(`Apollo curation ${res.status}`);
  const status = String(raw.status);
  const keptRaw = Array.isArray(raw.kept) ? raw.kept : null;
  const failedRaw = Array.isArray(raw.failed) ? raw.failed : null;
  const duplicatesRaw = Array.isArray(raw.duplicates) ? raw.duplicates : null;
  const originalRaw = record(raw.original_session);
  if (
    !["success", "partial", "failed"].includes(status) ||
    !Number.isInteger(raw.selected_row_count) ||
    !Number.isInteger(raw.selected_identity_count) ||
    !keptRaw ||
    !failedRaw ||
    !duplicatesRaw ||
    !originalRaw
  ) {
    throw new Error("Apollo curation payload malformed");
  }
  const nullableText = (value: unknown): string | null =>
    typeof value === "string" && value ? value : null;
  const kept = keptRaw.map((value) => {
    const item = record(value);
    const chat = item.sourcing_chat === null ? null : record(item.sourcing_chat);
    if (
      !Number.isInteger(item.row_index) ||
      typeof item.apollo_id !== "string" ||
      !item.apollo_id ||
      typeof item.person_id !== "string" ||
      !item.person_id ||
      !Number.isInteger(item.version) ||
      !["created", "updated"].includes(String(item.operation)) ||
      (chat && (typeof chat.session_id !== "string" || !chat.session_id))
    ) {
      throw new Error("Apollo curation payload malformed");
    }
    const rawLastName = nullableText(item.last_name);
    const lastNameIsMasked = Boolean(rawLastName?.includes("*"));
    const lastNameStatus = ["known", "hidden_by_apollo", "missing"].includes(
      String(item.last_name_status),
    )
      ? String(item.last_name_status)
      : lastNameIsMasked
        ? "hidden_by_apollo"
        : rawLastName
          ? "known"
          : "missing";
    return {
      row_index: item.row_index as number,
      apollo_id: item.apollo_id,
      person_id: item.person_id,
      version: item.version as number,
      operation: item.operation as "created" | "updated",
      first_name: nullableText(item.first_name),
      last_name: lastNameIsMasked ? null : rawLastName,
      last_name_status: lastNameStatus as ApolloCurationKept["last_name_status"],
      title: nullableText(item.title),
      company: nullableText(item.company),
      sourcing_chat: chat ? { session_id: chat.session_id as string } : null,
    };
  });
  const failed = failedRaw.map((value) => {
    const item = record(value);
    if (!Number.isInteger(item.row_index) || typeof item.code !== "string") {
      throw new Error("Apollo curation payload malformed");
    }
    return {
      row_index: item.row_index as number,
      apollo_id: nullableText(item.apollo_id),
      code: item.code,
    };
  });
  const duplicates = duplicatesRaw.map((value) => {
    const item = record(value);
    if (!Number.isInteger(item.row_index) || typeof item.apollo_id !== "string") {
      throw new Error("Apollo curation payload malformed");
    }
    return { row_index: item.row_index as number, apollo_id: item.apollo_id };
  });
  if (
    typeof originalRaw.session_id !== "string" ||
    (originalRaw.bound_person_id !== null &&
      typeof originalRaw.bound_person_id !== "string") ||
    typeof originalRaw.reason !== "string"
  ) {
    throw new Error("Apollo curation payload malformed");
  }
  return {
    status: status as ApolloCurationResult["status"],
    selected_row_count: raw.selected_row_count as number,
    selected_identity_count: raw.selected_identity_count as number,
    kept,
    failed,
    duplicates,
    original_session: {
      session_id: originalRaw.session_id,
      bound_person_id: originalRaw.bound_person_id as string | null,
      reason: originalRaw.reason,
    },
  };
}

export type OutreachDraft = {
  readonly id: string;
  readonly to: string;
  readonly subject: string;
  readonly body: string;
  readonly body_digest: string;
  readonly account: string | null;
  readonly sent: boolean;
};

/** The binding a send approval carries. Never the body, only its digest. */
export type SendAuthorityResource = {
  readonly kind: "gmail_send_authority";
  readonly person_id: string;
  readonly draft_id: string;
  readonly account: string | null;
  readonly to: string;
  readonly subject: string;
  readonly body_digest: string;
};

export type PendingSendApproval = {
  readonly id: string;
  readonly name: string;
  readonly state: string;
  readonly resource: SendAuthorityResource;
};

async function personPost(
  id: string,
  path: string,
  payload: Record<string, unknown>,
): Promise<unknown> {
  const res = await fetch(
    `${httpBase()}/v1/people/${encodeURIComponent(id)}/${path}`,
    {
      method: "POST",
      headers: { "X-Club-Token": apiToken(), "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || `${path} ${res.status}`);
  return body;
}

export async function createOutreachDraft(
  personId: string,
  input: { sessionId: string; subject: string; body: string },
): Promise<OutreachDraft> {
  const body = await personPost(personId, "outreach/draft", {
    session_id: input.sessionId,
    subject: input.subject,
    body: input.body,
  });
  return (body as { draft: OutreachDraft }).draft;
}

export async function readOutreachDraft(
  personId: string,
  draftId: string,
): Promise<OutreachDraft> {
  const res = await get(
    `/v1/people/${encodeURIComponent(personId)}/outreach/draft/${encodeURIComponent(draftId)}`,
  );
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || `draft ${res.status}`);
  return (body as { draft: OutreachDraft }).draft;
}

export async function requestSendApproval(
  personId: string,
  input: { sessionId: string; draftId: string; reviewedBodyDigest: string },
): Promise<PendingSendApproval> {
  const body = await personPost(personId, "outreach/send-approval", {
    session_id: input.sessionId,
    draft_id: input.draftId,
    reviewed_body_digest: input.reviewedBodyDigest,
  });
  return (body as { item: PendingSendApproval }).item;
}

export async function requestEnrichApproval(
  personId: string,
  sessionId: string,
): Promise<{ readonly id: string; readonly reason: string }> {
  const body = await personPost(personId, "enrich-approval", {
    session_id: sessionId,
  });
  const item = (body as { item: { id: string; reason: string } }).item;
  return { id: item.id, reason: item.reason };
}

export async function decideApproval(
  id: string,
  decision: "allow" | "deny",
): Promise<{ readonly ok: boolean; readonly result: Record<string, unknown> }> {
  const res = await fetch(`${httpBase()}/v1/inbox/${encodeURIComponent(id)}`, {
    method: "POST",
    headers: { "X-Club-Token": apiToken(), "Content-Type": "application/json" },
    body: JSON.stringify({ decision, actor: "director", scope: "once" }),
  });
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || `approval ${res.status}`);
  return body;
}

export async function setPersonSequence(
  id: string,
  state: string,
  actor = "director"
): Promise<{ person: BoardPerson }> {
  const res = await fetch(`${httpBase()}/v1/people/${encodeURIComponent(id)}/sequence`, {
    method: "POST",
    headers: { "X-Club-Token": apiToken(), "Content-Type": "application/json" },
    body: JSON.stringify({ state, actor }),
  });
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || `sequence ${res.status}`);
  return body;
}

export async function revertPerson(
  id: string,
  body: { toVersion: number; expectedVersion: number; rationaleSummary: string },
): Promise<{ person: BoardPerson }> {
  const res = await fetch(`${httpBase()}/v1/people/${encodeURIComponent(id)}/revert`, {
    method: "POST",
    headers: { "X-Club-Token": apiToken(), "Content-Type": "application/json" },
    body: JSON.stringify({
      to_version: body.toVersion,
      expected_version: body.expectedVersion,
      rationale_summary: body.rationaleSummary,
    }),
  });
  const payload = await res.json();
  if (!res.ok) throw new Error(payload.error || `revert ${res.status}`);
  return payload;
}

export async function savePersonHandoff(
  id: string,
  body: {
    who: string;
    wanted: string;
    happened: string;
    theyWant: string;
    expectedVersion: number;
  },
): Promise<{
  person: PersonFile["person"];
  brief: LivingBrief;
  saved: boolean;
  unchanged: boolean;
}> {
  const res = await fetch(`${httpBase()}/v1/people/${encodeURIComponent(id)}/handoff`, {
    method: "POST",
    headers: { "X-Club-Token": apiToken(), "Content-Type": "application/json" },
    body: JSON.stringify({
      who: body.who,
      wanted: body.wanted,
      happened: body.happened,
      they_want: body.theyWant,
      expected_version: body.expectedVersion,
    }),
  });
  const payload = await res.json();
  if (!res.ok) throw new Error(payload.error || `handoff ${res.status}`);
  return payload;
}

export async function pinSession(
  id: string,
  pinned: boolean,
): Promise<{ id: string; title: string | null; pinned: boolean }> {
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "X-Club-Token": apiToken(), "Content-Type": "application/json" },
    body: JSON.stringify({ pinned }),
  });
  if (!res.ok) throw new Error(`pin ${res.status}`);
  return res.json();
}

export async function getPersona(): Promise<PersonaInfo> {
  const res = await get("/v1/persona");
  if (!res.ok) throw new Error(`persona ${res.status}`);
  return res.json();
}

export type SkillInfo = { name: string; description: string };

export async function getSkills(): Promise<{ skills: SkillInfo[] }> {
  const res = await get("/v1/skills");
  if (!res.ok) throw new Error(`skills ${res.status}`);
  return res.json();
}

export type MemoryRow = {
  id: number;
  content: string;
  category: string;
  classification_status: string | null;
  created_at: string;
};

export type MemoryBacklog = {
  needs_review: number;
  classified: number;
  items: MemoryRow[];
};

export async function getMemoryBacklog(): Promise<MemoryBacklog> {
  const res = await get("/v1/memory/classification");
  if (!res.ok) throw new Error(`memory ${res.status}`);
  return res.json();
}

export async function classifyMemory(id: number): Promise<{ memory: MemoryRow }> {
  const res = await fetch(`${httpBase()}/v1/memory/${id}/classification`, {
    method: "POST",
    headers: { "X-Club-Token": apiToken(), "Content-Type": "application/json" },
    body: JSON.stringify({ category: "operator_preference" }),
  });
  if (!res.ok) throw new Error(`memory classify ${res.status}`);
  return res.json();
}

export async function forgetMemory(id: number): Promise<{ forgotten: boolean; id: number }> {
  const res = await fetch(`${httpBase()}/v1/memory/${id}`, {
    method: "DELETE",
    headers: { "X-Club-Token": apiToken() },
  });
  if (!res.ok) throw new Error(`memory forget ${res.status}`);
  return res.json();
}

const SCHEDULE_RUN_STATUSES = new Set<ScheduleRunStatus>([
  "running",
  "success",
  "failed",
  "waiting_approval",
  "partial",
  "interrupted",
]);

function numberValue(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function normalizeScheduleJob(value: unknown): ScheduleJob {
  const raw = record(value);
  return {
    id: numberValue(raw.id),
    name: text(raw.name, text(raw.prompt, "Routine")),
    templateId: text(raw.template_id, "legacy"),
    cadence: text(raw.cadence, text(raw.cron)),
    cron: text(raw.cron),
    prompt: text(raw.prompt),
    createdAt: text(raw.created_at),
    nextRunAt: typeof raw.next_run_at === "string" ? raw.next_run_at : null,
  };
}

function normalizeScheduleArtifact(value: unknown): ScheduleArtifact {
  const raw = record(value);
  let externalUrl: string | null = null;
  if (typeof raw.external_url === "string") {
    try {
      const parsed = new URL(raw.external_url);
      if (parsed.protocol === "http:" || parsed.protocol === "https:") {
        externalUrl = parsed.toString();
      }
    } catch {
      externalUrl = null;
    }
  }
  return {
    id: text(raw.id, "artifact"),
    artifactType: text(raw.artifact_type, "artifact"),
    title: text(raw.title, "Generated artifact"),
    externalUrl,
  };
}

function normalizeScheduleRun(value: unknown): ScheduleRun {
  const raw = record(value);
  const rawStatus = text(raw.status) as ScheduleRunStatus;
  // A status this client has never seen is not the same as a failure: it is
  // an indeterminate outcome, and must read as such instead of falsely
  // "Failed". (Legacy "ok" rows are normalized to "success" on disk, so they
  // never reach this fallback.)
  const status = SCHEDULE_RUN_STATUSES.has(rawStatus) ? rawStatus : "unknown";
  return {
    id: numberValue(raw.id),
    jobId: numberValue(raw.job_id),
    status,
    result: text(raw.result),
    summary: text(raw.summary, text(raw.result)),
    createdAt: text(raw.created_at),
    startedAt: text(raw.started_at, text(raw.created_at)),
    finishedAt: typeof raw.finished_at === "string" ? raw.finished_at : null,
    durationMs: numberValue(raw.duration_ms),
    sessionId: text(raw.session_id, `sched-${numberValue(raw.job_id)}`),
    waitingApprovalCount: numberValue(raw.waiting_approval_count),
    artifacts: Array.isArray(raw.artifacts)
      ? raw.artifacts.map(normalizeScheduleArtifact)
      : [],
  };
}

function normalizeScheduleTemplate(value: unknown): ScheduleTemplate {
  const raw = record(value);
  return {
    id: text(raw.id),
    name: text(raw.name, "Routine template"),
    description: text(raw.description),
    cadences: textList(raw.cadences),
    defaultPrompt: text(raw.default_prompt),
  };
}

export class ScheduleApiError extends Error {
  code: "invalid_routine" | "already_running" | "request_failed";
  fieldErrors: Record<string, string>;

  constructor(
    code: "invalid_routine" | "already_running" | "request_failed",
    message: string,
    fieldErrors: Record<string, string> = {},
  ) {
    super(message);
    this.name = "ScheduleApiError";
    this.code = code;
    this.fieldErrors = fieldErrors;
  }
}

function safeFieldErrors(value: unknown): Record<string, string> {
  const raw = record(value);
  return Object.fromEntries(
    Object.entries(raw).filter((entry): entry is [string, string] => typeof entry[1] === "string"),
  );
}

export async function getSchedule(): Promise<Schedule> {
  const res = await get("/v1/schedule");
  if (!res.ok) throw new Error(`schedule ${res.status}`);
  const body = record(await res.json());
  return {
    jobs: Array.isArray(body.jobs) ? body.jobs.map(normalizeScheduleJob) : [],
    runs: Array.isArray(body.runs) ? body.runs.map(normalizeScheduleRun) : [],
    templates: Array.isArray(body.templates)
      ? body.templates.map(normalizeScheduleTemplate)
      : [],
  };
}

export async function createScheduleJob(input: {
  templateId: string;
  cadence: string;
  name: string;
  prompt: string;
}): Promise<ScheduleJob> {
  const res = await fetch(`${httpBase()}/v1/schedule`, {
    method: "POST",
    headers: { "X-Club-Token": apiToken(), "Content-Type": "application/json" },
    body: JSON.stringify({
      template_id: input.templateId,
      cadence: input.cadence,
      name: input.name,
      prompt: input.prompt,
    }),
  });
  const body = record(await res.json());
  if (!res.ok) {
    if (body.error === "invalid_routine") {
      throw new ScheduleApiError(
        "invalid_routine",
        "Review the highlighted routine fields.",
        safeFieldErrors(body.fields),
      );
    }
    throw new ScheduleApiError("request_failed", "The routine could not be created.");
  }
  return normalizeScheduleJob(body.job);
}

export async function runScheduleJob(id: number): Promise<{ run: ScheduleRun }> {
  const res = await fetch(`${httpBase()}/v1/schedule/${id}/run`, {
    method: "POST",
    headers: { "X-Club-Token": apiToken() },
  });
  const body = record(await res.json());
  if (!res.ok) {
    if (body.error === "already_running") {
      throw new ScheduleApiError(
        "already_running",
        "This routine is already running. Wait for its current receipt.",
      );
    }
    throw new ScheduleApiError("request_failed", "The routine could not be started.");
  }
  return { run: normalizeScheduleRun(body.run) };
}

export type GmailStatus = { connected: boolean; email: string | null };

export async function getGmail(): Promise<GmailStatus> {
  const res = await get("/v1/gmail");
  if (!res.ok) throw new Error(`gmail ${res.status}`);
  return res.json();
}

export async function connectGmail(): Promise<{ url: string; opened?: boolean; redirect_uri?: string }> {
  const res = await fetch(`${httpBase()}/v1/gmail/connect`, {
    method: "POST",
    headers: { "X-Club-Token": apiToken() },
  });
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || `gmail connect ${res.status}`);
  return body;
}

export type Settings = {
  persona: { id: string; name: string };
  model: string | null;
  gmail: GmailStatus;
  apollo: { configured: boolean };
  providers: ProviderVerification[];
  workspace?: WorkspaceDiagnostics;
};

export type ProviderVerification = {
  provider: "deepseek" | "kimi" | "anthropic" | "openai";
  model: string | null;
  selected: boolean;
  eligible: boolean;
  failures: string[];
  context_window_tokens: number | null;
  capabilities: {
    text: boolean;
    transient_reasoning: boolean;
    tool_calling: boolean;
    terminal_usage: boolean;
    cache_usage: boolean;
    reasoning_usage: boolean;
  };
};

const PROVIDER_IDS = new Set(["deepseek", "kimi", "anthropic", "openai"]);
const PROVIDER_FAILURES = new Set([
  "missing_credentials",
  "invalid_base_url",
  "unsupported_model",
  "provider_contract_unverified",
  "invalid_model_identifier",
]);
const SAFE_MODEL = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/;
const SECRET_MODEL_PREFIXES = [
  "sk-",
  "ghp_",
  "github_pat_",
  "ya29.",
  "aiza",
  "xoxb-",
  "xoxp-",
  "xoxa-",
  "xoxr-",
  "xapp-",
];

function safeModel(value: unknown): string | null {
  if (typeof value !== "string" || !SAFE_MODEL.test(value)) return null;
  const lowered = value.toLowerCase();
  if (SECRET_MODEL_PREFIXES.some((prefix) => lowered.startsWith(prefix))) {
    return null;
  }
  if (lowered.startsWith("file:/") || lowered.includes("://")) return null;
  if (/^[A-Za-z]:\//.test(value)) return null;
  if (value.split("/").some((segment) => ["", ".", ".."].includes(segment))) {
    return null;
  }
  return value;
}

function providerVerification(value: unknown): ProviderVerification | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return null;
  }
  const raw = value as Record<string, unknown>;
  if (typeof raw.provider !== "string" || !PROVIDER_IDS.has(raw.provider)) {
    return null;
  }
  const capabilities =
    typeof raw.capabilities === "object" &&
    raw.capabilities !== null &&
    !Array.isArray(raw.capabilities)
      ? (raw.capabilities as Record<string, unknown>)
      : {};
  const model = safeModel(raw.model);
  return {
    provider: raw.provider as ProviderVerification["provider"],
    model,
    selected: raw.selected === true && model !== null,
    eligible: raw.eligible === true && model !== null,
    failures: Array.isArray(raw.failures)
      ? raw.failures.filter(
          (failure): failure is string =>
            typeof failure === "string" && PROVIDER_FAILURES.has(failure),
        )
      : [],
    context_window_tokens: measurement(raw.context_window_tokens),
    capabilities: {
      text: capabilities.text === true,
      transient_reasoning: capabilities.transient_reasoning === true,
      tool_calling: capabilities.tool_calling === true,
      terminal_usage: capabilities.terminal_usage === true,
      cache_usage: capabilities.cache_usage === true,
      reasoning_usage: capabilities.reasoning_usage === true,
    },
  };
}

export async function getSettings(): Promise<Settings> {
  const res = await get("/v1/settings");
  if (!res.ok) throw new Error(`settings ${res.status}`);
  const raw = (await res.json()) as Record<string, unknown>;
  const persona = (raw.persona ?? {}) as Record<string, unknown>;
  const gmail = (raw.gmail ?? {}) as Record<string, unknown>;
  const apollo = (raw.apollo ?? {}) as Record<string, unknown>;
  const providers = Array.isArray(raw.providers)
    ? raw.providers
        .map(providerVerification)
        .filter((provider): provider is ProviderVerification => provider !== null)
    : [];
  return {
    persona: {
      id: typeof persona.id === "string" ? persona.id : "",
      name: typeof persona.name === "string" ? persona.name : "",
    },
    model: safeModel(raw.model),
    gmail: {
      connected: gmail.connected === true,
      email: typeof gmail.email === "string" ? gmail.email : null,
    },
    apollo: { configured: apollo.configured === true },
    providers,
    ...(
      typeof raw.workspace === "object" && raw.workspace !== null
        ? { workspace: raw.workspace as WorkspaceDiagnostics }
        : {}
    ),
  };
}

export async function setPersona(id: string): Promise<{ persona: { id: string; name: string } }> {
  const res = await fetch(`${httpBase()}/v1/settings/persona`, {
    method: "POST",
    headers: { "X-Club-Token": apiToken(), "Content-Type": "application/json" },
    body: JSON.stringify({ id }),
  });
  if (!res.ok) throw new Error(`persona ${res.status}`);
  return res.json();
}

export type InboxItem = {
  id: string;
  kind: string;
  name: string;
  arguments: Record<string, unknown>;
  state: string;
  decision?: string | null;
  session_id?: string | null;
  run_id?: string | null;
};

export async function getInbox(): Promise<{ items: InboxItem[] }> {
  const res = await get("/v1/inbox");
  if (!res.ok) throw new Error(`inbox ${res.status}`);
  return res.json();
}

export async function resolveInbox(
  id: string,
  decision: "allow" | "deny",
  scope: "once" | "always" = "once",
): Promise<void> {
  const res = await fetch(`${httpBase()}/v1/inbox/${id}`, {
    method: "POST",
    headers: { "X-Club-Token": apiToken(), "Content-Type": "application/json" },
    body: JSON.stringify({ decision, scope }),
  });
  if (!res.ok) throw new Error(`inbox ${res.status}`);
}

export type WorkspaceGrant = {
  id: string;
  path: string;
  label: string;
  access: "read_only" | "read_write";
  allow_shell: boolean;
  filesystem_identity: { device: number; inode: number };
  created_at: string;
  updated_at: string;
  revoked_at: string | null;
  stopped_task_ids?: string[];
};

export type DirectoryRequest = {
  id: string;
  label: string;
  access: "read_only" | "read_write";
  allow_shell: boolean;
  session_id: string | null;
  run_id: string | null;
  created_at: string;
  resolved_at: string | null;
  grant_id: string | null;
};

export type HostCommandApproval = {
  id: string;
  fingerprint: string;
  command_summary: string;
  command_display?: string;
  executable?: { path: string; sha256: string } | null;
  scripts?: Array<{ path: string; sha256: string }>;
  cwd: string;
  created_at: string;
  revoked_at: string | null;
};

export type ShellTask = {
  task_id: string;
  grant_id: string;
  status: string;
  execution_target: "docker" | "host";
  command_summary: string;
  started_at: string;
  finished_at?: string | null;
  exit_code?: number | null;
  error?: string;
};

export type WorkspaceDiagnostics = {
  docker: {
    cli_available: boolean;
    daemon_available: boolean;
    image_available: boolean;
    available: boolean;
    image: string;
    network: "unrestricted";
    server_version?: string | null;
  };
  execution_target: "docker" | "host_fallback";
  host_fallback_enabled: boolean;
  grants: WorkspaceGrant[];
  directory_requests: DirectoryRequest[];
  host_approvals: HostCommandApproval[];
  tasks: ShellTask[];
};

export type WorkspaceGrantInput = {
  path: string;
  label: string;
  access: "read_only" | "read_write";
  allow_shell: boolean;
  request_id?: string;
};

async function workspaceMutation<T>(path: string, method: string, body?: unknown): Promise<T> {
  const res = await fetch(`${httpBase()}${path}`, {
    method,
    headers: {
      "X-Club-Token": apiToken(),
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const payload = await res.json();
  if (!res.ok) throw new Error(payload.error || `workspace ${res.status}`);
  return payload as T;
}

export async function createWorkspaceGrant(
  input: WorkspaceGrantInput,
): Promise<{ grant: WorkspaceGrant }> {
  return workspaceMutation("/v1/workspaces", "POST", input);
}

export async function updateWorkspaceGrant(
  id: string,
  changes: Partial<Pick<WorkspaceGrantInput, "path" | "label" | "access" | "allow_shell">>,
): Promise<{ grant: WorkspaceGrant }> {
  return workspaceMutation(`/v1/workspaces/${encodeURIComponent(id)}`, "PATCH", changes);
}

export async function revokeWorkspaceGrant(
  id: string,
): Promise<{ grant: WorkspaceGrant }> {
  return workspaceMutation(`/v1/workspaces/${encodeURIComponent(id)}`, "DELETE");
}

export async function revokeHostApproval(
  id: string,
): Promise<{ approval: HostCommandApproval }> {
  return workspaceMutation(
    `/v1/workspaces/host-approvals/${encodeURIComponent(id)}`,
    "DELETE",
  );
}

export async function cancelShellTask(id: string): Promise<{ task: ShellTask }> {
  return workspaceMutation(
    `/v1/workspaces/tasks/${encodeURIComponent(id)}/cancel`,
    "POST",
  );
}

export async function pickDirectory(): Promise<string | null> {
  const tauri = (window as unknown as {
    __TAURI__?: { dialog?: { open?: (options: unknown) => Promise<unknown> } };
  }).__TAURI__;
  if (!tauri?.dialog?.open) {
    throw new Error("The native folder picker is unavailable.");
  }
  const selected = await tauri.dialog.open({ directory: true, multiple: false });
  return typeof selected === "string" ? selected : null;
}

export type ConnectorId = "gmail" | "drive" | "calendar" | "apollo" | "granola";
export type ConnectorStatus =
  | "connected"
  | "available"
  | "authorizing"
  | "missing_scopes"
  | "degraded"
  | "failed"
  | "reconnect_required";
export type ConnectorCatalogGroup = "connected" | "available";
export type ConnectorAction =
  | "connect"
  | "reconnect"
  | "disconnect"
  | "view_guidance";

export type Connector = {
  id: ConnectorId;
  title: string;
  description: string;
  status: ConnectorStatus;
  catalogGroup: ConnectorCatalogGroup;
  email: string | null;
  requiredScopes: string[];
  missingScopes: string[];
  health: { category: string; label: string; message: string };
  recovery: { category: string; actionLabel: string; message: string } | null;
  supportedActions: string[];
  availableActions: ConnectorAction[];
  repairRoute: string;
  authorizationGroup: "google" | "granola" | null;
};

const CONNECTOR_IDS = new Set<ConnectorId>([
  "gmail",
  "drive",
  "calendar",
  "apollo",
  "granola",
]);
const CONNECTOR_STATUSES = new Set<ConnectorStatus>([
  "connected",
  "available",
  "authorizing",
  "missing_scopes",
  "degraded",
  "failed",
  "reconnect_required",
]);
const CONNECTOR_ACTIONS = new Set<ConnectorAction>([
  "connect",
  "reconnect",
  "disconnect",
  "view_guidance",
]);

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function text(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function textList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function normalizeConnector(value: unknown): Connector | null {
  const raw = record(value);
  const id = text(raw.id) as ConnectorId;
  if (!CONNECTOR_IDS.has(id)) return null;
  const rawStatus = text(raw.status) as ConnectorStatus;
  const status = CONNECTOR_STATUSES.has(rawStatus) ? rawStatus : "failed";
  const rawGroup = text(raw.catalog_group);
  const catalogGroup: ConnectorCatalogGroup =
    rawGroup === "connected" || rawGroup === "available"
      ? rawGroup
      : status === "available" || status === "failed"
        ? "available"
        : "connected";
  const rawHealth = record(raw.health);
  const rawRecovery = raw.recovery === null ? null : record(raw.recovery);
  const authorizationGroup = text(raw.authorization_group);
  return {
    id,
    title: text(raw.title, id),
    description: text(raw.description),
    status,
    catalogGroup,
    email: typeof raw.email === "string" ? raw.email : null,
    requiredScopes: textList(raw.required_scopes),
    missingScopes: textList(raw.missing_scopes),
    health: {
      category: text(rawHealth.category, "unknown"),
      label: text(rawHealth.label, "Needs attention"),
      message: text(rawHealth.message, "Refresh this connection to check its status."),
    },
    recovery:
      rawRecovery === null
        ? null
        : {
            category: text(rawRecovery.category, "retry"),
            actionLabel: text(rawRecovery.action_label, "Try again"),
            message: text(rawRecovery.message, "Try the connection again."),
          },
    supportedActions: textList(raw.supported_actions),
    availableActions: textList(raw.available_actions).filter(
      (action): action is ConnectorAction => CONNECTOR_ACTIONS.has(action as ConnectorAction),
    ),
    repairRoute: `#/connections/${encodeURIComponent(id)}`,
    authorizationGroup:
      authorizationGroup === "google" || authorizationGroup === "granola"
        ? authorizationGroup
        : null,
  };
}

export async function getConnectors(): Promise<{ connectors: Connector[] }> {
  const res = await get("/v1/connectors");
  if (!res.ok) throw new Error(`connectors ${res.status}`);
  const body = record(await res.json());
  const connectors = Array.isArray(body.connectors)
    ? body.connectors.map(normalizeConnector).filter((item): item is Connector => item !== null)
    : [];
  return { connectors };
}

export async function connectDrive(): Promise<{ url: string; opened?: boolean }> {
  const res = await fetch(`${httpBase()}/v1/connectors/drive/connect`, {
    method: "POST",
    headers: { "X-Club-Token": apiToken() },
  });
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || `drive connect ${res.status}`);
  return body;
}

export async function connectCalendar(): Promise<{ url: string; opened?: boolean }> {
  const res = await fetch(`${httpBase()}/v1/connectors/calendar/connect`, {
    method: "POST",
    headers: { "X-Club-Token": apiToken() },
  });
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || `calendar connect ${res.status}`);
  return body;
}

export async function connectGranola(): Promise<{ started: boolean; url?: string }> {
  const res = await fetch(`${httpBase()}/v1/connectors/granola/connect`, {
    method: "POST",
    headers: { "X-Club-Token": apiToken() },
  });
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || `granola connect ${res.status}`);
  return body;
}

export type ConnectorAuthorization = {
  url?: string;
  opened?: boolean;
  started?: boolean;
  redirect_uri?: string;
};

export async function connectConnector(
  id: Exclude<ConnectorId, "apollo">,
): Promise<ConnectorAuthorization> {
  if (id === "gmail") return connectGmail();
  if (id === "drive") return connectDrive();
  if (id === "calendar") return connectCalendar();
  return connectGranola();
}

export async function disconnectConnector(
  id: Exclude<ConnectorId, "apollo">,
): Promise<{ connected: false; disconnected: ConnectorId[] }> {
  const path =
    id === "granola" ? "/v1/connectors/granola/disconnect" : "/v1/gmail/disconnect";
  const res = await fetch(`${httpBase()}${path}`, {
    method: "POST",
    headers: { "X-Club-Token": apiToken() },
  });
  if (!res.ok) throw new Error(`connector disconnect ${res.status}`);
  const body = record(await res.json());
  const disconnected = textList(body.disconnected).filter((item): item is ConnectorId =>
    CONNECTOR_IDS.has(item as ConnectorId),
  );
  return { connected: false, disconnected };
}

export async function disconnectGmail(): Promise<GmailStatus> {
  const res = await fetch(`${httpBase()}/v1/gmail/disconnect`, {
    method: "POST",
    headers: { "X-Club-Token": apiToken() },
  });
  if (!res.ok) throw new Error(`gmail disconnect ${res.status}`);
  return res.json();
}

export function hasToken(): boolean {
  return apiToken().length > 0;
}

const RECONNECT_BASE_MS = 500;
const RECONNECT_MAX_MS = 10_000;
const PENDING_COMMAND_LIMIT = 50;

/**
 * Synchronous fate of a command handed to the transport. "delivered" means it
 * was written to an open socket now; "queued" means it is buffered and will
 * flush on reconnect; "dropped" means it will never be sent — callers must
 * surface that to the operator rather than implying the command is in flight.
 */
export type CommandDelivery =
  | { readonly state: "delivered" }
  | { readonly state: "queued" }
  | { readonly state: "dropped"; readonly reason: string };

export function openChat(onEvent: (event: SourcecadoSocketEvent) => void): {
  send: (text: string, sessionId: string) => CommandDelivery;
  cancel: (sessionId: string, runId: string) => CommandDelivery;
  queue: (command: QueueCommand) => CommandDelivery;
  recover: (command: RecoveryCommand) => CommandDelivery;
  approve: (id: string, decision: "allow" | "deny") => CommandDelivery;
  close: () => void;
} {
  const token = apiToken();
  const protocols = token ? ["club", token] : ["club"];
  let ws: WebSocket | null = null;
  let disposed = false;
  let terminal = false;
  let attempt = 0;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  const pending: unknown[] = [];
  const queueSnapshots = new Map<string, QueueSnapshotEvent>();
  let syntheticCommandNumber = 0;
  // Reconnect re-sync bookkeeping: which runs are open on which session, the
  // last event id delivered per session, and sessions with a delivered chat
  // command still waiting for its turn_start. A run that reaches a terminal
  // state while the socket is down would otherwise stay "running" forever.
  const openRuns = new Map<string, Set<string>>();
  const lastDeliveredEvent = new Map<string, string>();
  const awaitingTurnStart = new Set<string>();
  let resyncDepth = 0;
  const resyncBuffer: SourcecadoSocketEvent[] = [];

  function trackEnvelope(event: ProtocolChatEvent) {
    lastDeliveredEvent.set(event.session_id, event.event_id);
    if (event.type === "turn_start") {
      const runs = openRuns.get(event.session_id) ?? new Set<string>();
      runs.add(event.run_id);
      openRuns.set(event.session_id, runs);
      awaitingTurnStart.delete(event.session_id);
      return;
    }
    if (
      event.type === "turn_end" ||
      event.type === "turn_stopped" ||
      event.type === "error"
    ) {
      openRuns.get(event.session_id)?.delete(event.run_id);
    }
  }

  function dispatch(event: SourcecadoSocketEvent) {
    if (event.type === "queue_snapshot") {
      queueSnapshots.set(event.session_id, event);
    } else if ("version" in event) {
      trackEnvelope(event);
    }
    onEvent(event);
  }

  // After a drop, fetch each interesting session's durable event log and
  // replay the tail this socket never delivered. The store dedupes by
  // event_id, so overlap with anything the sidecar replays itself is safe;
  // live socket events are held until the tail lands to preserve order.
  async function resyncMissedEvents() {
    const sessions = new Set<string>(awaitingTurnStart);
    for (const [sessionId, runs] of openRuns) {
      if (runs.size > 0) sessions.add(sessionId);
    }
    if (sessions.size === 0) return;
    resyncDepth += 1;
    try {
      for (const sessionId of sessions) {
        try {
          const conversation = await getSession(sessionId);
          if (disposed) return;
          const events = conversation.events.filter(
            (event): event is ProtocolChatEvent => "version" in event,
          );
          const lastId = lastDeliveredEvent.get(sessionId);
          const lastIndex =
            lastId === undefined
              ? -1
              : events.findIndex((event) => event.event_id === lastId);
          for (const event of events.slice(lastIndex + 1)) dispatch(event);
          awaitingTurnStart.delete(sessionId);
        } catch {
          if (disposed) return;
          onEvent({
            type: "error",
            message:
              "Reconnected, but the conversation re-sync failed. Reload to see the latest run state.",
            session_id: sessionId,
          });
        }
      }
    } finally {
      resyncDepth -= 1;
      if (resyncDepth === 0 && !disposed) {
        for (const event of resyncBuffer.splice(0)) dispatch(event);
      }
    }
  }

  // Re-emits the last authoritative queue snapshot per session; while the
  // socket is down, deliverable items are shown as offline/reconnecting.
  function emitQueueStates(state?: "offline" | "reconnecting") {
    for (const snapshot of queueSnapshots.values()) {
      onEvent({
        ...snapshot,
        command_id: `connection-${++syntheticCommandNumber}`,
        status: "connection",
        items: state
          ? snapshot.items.map((item) =>
              item.state === "waiting" || item.state === "sending"
                ? { ...item, state }
                : item,
            )
          : snapshot.items,
      });
    }
  }

  function scheduleReconnect() {
    const delay = Math.min(RECONNECT_BASE_MS * 2 ** attempt, RECONNECT_MAX_MS);
    attempt += 1;
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      emitQueueStates("reconnecting");
      connect();
    }, delay);
  }

  function connect() {
    const socket = new WebSocket(`${wsBase()}/ws/chat`, protocols);
    ws = socket;
    socket.onopen = () => {
      if (disposed) return;
      attempt = 0;
      onEvent({
        type: "connection_change",
        status: "connected",
        attempt: 0,
        reason: "Connected to the sidecar.",
      });
      emitQueueStates();
      for (const payload of pending.splice(0)) {
        socket.send(JSON.stringify(payload));
        const command = payload as { type?: unknown; session_id?: unknown };
        if (
          command.type === "chat" &&
          typeof command.session_id === "string"
        ) {
          awaitingTurnStart.add(command.session_id);
        }
      }
      void resyncMissedEvents();
    };
    socket.onmessage = (ev) => {
      try {
        const parsed = parseSocketEvent(JSON.parse(String(ev.data)));
        if (resyncDepth > 0) {
          resyncBuffer.push(parsed);
          return;
        }
        dispatch(parsed);
      } catch {
        onEvent(parseChatEvent(undefined));
      }
    };
    socket.onclose = (ev) => {
      if (disposed || socket !== ws) return;
      if (ev.code === 1008) {
        terminal = true;
        onEvent({ type: "error", message: "sidecar rejected the socket (token)" });
        if (pending.length > 0) {
          onEvent({
            type: "error",
            message: `${pending.length} queued command${
              pending.length === 1 ? "" : "s"
            } dropped because the sidecar rejected the connection token.`,
          });
          pending.length = 0;
        }
        onEvent({
          type: "connection_change",
          status: "offline",
          attempt,
          reason: "The sidecar rejected the connection token.",
        });
        emitQueueStates("offline");
        return;
      }
      onEvent({
        type: "connection_change",
        status: "reconnecting",
        attempt: attempt + 1,
        reason: `The sidecar connection closed (code ${ev.code}). Reconnecting.`,
      });
      emitQueueStates("offline");
      scheduleReconnect();
    };
  }

  function push(payload: unknown): CommandDelivery {
    if (disposed) {
      return {
        state: "dropped",
        reason: "The chat connection is closed; the command was dropped.",
      };
    }
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(payload));
      return { state: "delivered" };
    }
    if (terminal) {
      const reason =
        "The sidecar rejected the connection token; the command was dropped.";
      onEvent({ type: "error", message: reason });
      return { state: "dropped", reason };
    }
    if (pending.length >= PENDING_COMMAND_LIMIT) {
      const reason =
        "The sidecar connection is down and the retry buffer is full; the command was dropped.";
      onEvent({ type: "error", message: reason });
      return { state: "dropped", reason };
    }
    pending.push(payload);
    return { state: "queued" };
  }

  connect();
  return {
    send(text: string, sessionId: string) {
      const delivery = push({ type: "chat", text, session_id: sessionId });
      // A delivered send may start a run whose turn_start we never see if the
      // socket drops immediately; remember it so reconnect re-syncs it.
      if (delivery.state === "delivered") awaitingTurnStart.add(sessionId);
      return delivery;
    },
    cancel(sessionId: string, runId: string) {
      return push({ type: "cancel", session_id: sessionId, run_id: runId });
    },
    queue(command: QueueCommand) {
      return push(command);
    },
    recover(command: RecoveryCommand) {
      return push(command);
    },
    approve(id: string, decision: "allow" | "deny") {
      return push({ type: "permission", id, decision });
    },
    close() {
      disposed = true;
      if (reconnectTimer !== null) clearTimeout(reconnectTimer);
      reconnectTimer = null;
      ws?.close();
    },
  };
}

// --- diagnostic bundle -----------------------------------------------------

export type DiagnosticEvidenceCategory = {
  id: string;
  title: string;
  description: string;
  included: boolean;
};

export type DiagnosticBundlePreview = {
  bundle_version: number;
  subject: { kind: string; run_id?: string; check?: string | null; store_id?: string | null };
  evidence_categories: DiagnosticEvidenceCategory[];
  excluded: string[];
  members: string[];
  counts: {
    log_records: number;
    findings: number;
    connectors: number;
    runs_considered: number;
  };
  run: {
    run_id: string | null;
    state: string | null;
    outcome_status: string | null;
    finished_at: string | null;
  } | null;
  state: { healthy: boolean | null; blocked: boolean | null };
  findings: {
    check: string | null;
    store_id: string | null;
    severity: string | null;
    summary: string | null;
  }[];
  log_records: Record<string, unknown>[];
};

export type DiagnosticBundleResult = {
  bundle_id: string;
  generated_at: string;
  path: string;
  sha256: string;
  size_bytes: number;
  members: string[];
};

/** A scan match. The sidecar never sends the matched value, only where it was. */
export type DiagnosticScanMatch = { category: string; location: string };

export type DiagnosticBundleStart = {
  run_id?: string;
  check?: string;
  store_id?: string;
};

export type DiagnosticBundleOutcome<T> =
  | { status: "ok"; value: T }
  | { status: "refused"; matches: DiagnosticScanMatch[] };

async function diagnosticsPost<T>(
  path: string,
  start: DiagnosticBundleStart,
  read: (payload: Record<string, unknown>) => T,
): Promise<DiagnosticBundleOutcome<T>> {
  const res = await fetch(`${httpBase()}${path}`, {
    method: "POST",
    headers: { "X-Club-Token": apiToken(), "Content-Type": "application/json" },
    body: JSON.stringify(start),
  });
  const payload = await res.json();
  if (res.status === 409 && payload?.error === "scan_refused") {
    return { status: "refused", matches: payload.matches ?? [] };
  }
  if (!res.ok) throw new Error(payload?.error || `diagnostics ${res.status}`);
  return { status: "ok", value: read(payload) };
}

export async function previewDiagnosticBundle(
  start: DiagnosticBundleStart,
): Promise<DiagnosticBundleOutcome<DiagnosticBundlePreview>> {
  return diagnosticsPost(
    "/v1/diagnostics/bundle/preview",
    start,
    (payload) => payload.preview as DiagnosticBundlePreview,
  );
}

export async function exportDiagnosticBundle(
  start: DiagnosticBundleStart,
): Promise<DiagnosticBundleOutcome<DiagnosticBundleResult>> {
  return diagnosticsPost(
    "/v1/diagnostics/bundle/export",
    start,
    (payload) => payload.bundle as DiagnosticBundleResult,
  );
}

// --- the external-effect review queue --------------------------------------
//
// An effect Sourcecado dispatched and never got an answer about. Neither
// "sent" nor "not sent": a person decides which, and until they do nothing
// retries it. `inboxClaim` is what the approval record says about the same
// action, kept separate on purpose -- when the two disagree the run store is
// the fence of record and `supersedesInbox` says so.

export type QuarantineDecision =
  | "resolved_succeeded"
  | "resolved_failed"
  | "abandoned";

export type QuarantinedEffect = {
  effectId: string;
  runId: string;
  toolName: string;
  approvalId: string | null;
  dispatchedAt: string | null;
  reason: string | null;
  status: string;
  inboxClaim: string | null;
  supersedesInbox: boolean;
  needsAPerson: boolean;
  sessionId: string | null;
  personId: string | null;
  approval: {
    id: string;
    name: string;
    requestedAt: string | null;
    resource: Record<string, unknown> | null;
  } | null;
};

function readQuarantinedEffect(raw: any): QuarantinedEffect {
  const approval = raw?.approval;
  return {
    effectId: String(raw?.effect_id || ""),
    runId: String(raw?.run_id || ""),
    toolName: String(raw?.tool_name || ""),
    approvalId: raw?.approval_id ?? null,
    dispatchedAt: raw?.dispatched_at ?? null,
    reason: raw?.reason ?? null,
    status: String(raw?.status || ""),
    inboxClaim: raw?.inbox_claim ?? null,
    supersedesInbox: Boolean(raw?.supersedes_inbox),
    needsAPerson: Boolean(raw?.needs_a_person),
    sessionId: raw?.session_id ?? null,
    personId: raw?.person_id ?? null,
    approval: approval
      ? {
          id: String(approval.id || ""),
          name: String(approval.name || ""),
          requestedAt: approval.requested_at ?? null,
          resource:
            approval.resource && typeof approval.resource === "object"
              ? (approval.resource as Record<string, unknown>)
              : null,
        }
      : null,
  };
}

export async function getQuarantinedEffects(): Promise<QuarantinedEffect[]> {
  const res = await get("/v1/agent-run-effects/quarantine");
  if (!res.ok) throw new Error(`quarantine ${res.status}`);
  const payload = await res.json();
  const rows = Array.isArray(payload?.effects) ? payload.effects : [];
  return rows.map(readQuarantinedEffect);
}

export async function settleQuarantinedEffect(
  effectId: string,
  decision: QuarantineDecision,
  operator: string,
  note?: string,
): Promise<void> {
  const res = await fetch(
    `${httpBase()}/v1/agent-run-effects/quarantine/${encodeURIComponent(effectId)}`,
    {
      method: "POST",
      headers: { "X-Club-Token": apiToken(), "Content-Type": "application/json" },
      body: JSON.stringify({ decision, operator, note }),
    },
  );
  const payload = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(payload?.error || `settle ${res.status}`);
}
