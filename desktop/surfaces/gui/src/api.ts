import {
  parseChatEvent,
  parseSocketEvent,
  type ChatEvent,
  type ChatEventEnvelope,
  type ProtocolChatEvent,
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
  | "partial";

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
  queue?: QueueItem[];
  queue_paused?: boolean;
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

export async function getSession(id: string): Promise<Conversation> {
  const res = await get(`/v1/sessions/${encodeURIComponent(id)}`);
  if (!res.ok) throw new Error(`session ${res.status}`);
  const body = await res.json();
  return {
    id: body.id,
    title: body.title,
    messages: body.messages,
    events: Array.isArray(body.events) ? body.events.map(parseChatEvent) : [],
    ...(Array.isArray(body.queue) ? { queue: body.queue } : {}),
    ...(typeof body.queue_paused === "boolean"
      ? { queue_paused: body.queue_paused }
      : {}),
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

export type BoardPerson = {
  person_id: string;
  first_name: string | null;
  last_name: string | null;
  title: string | null;
  company: string | null;
  sequence_state: string | null;
};

export type Board = {
  open: BoardPerson[];
  in_conversation: BoardPerson[];
  done: BoardPerson[];
};

export type PersonFile = {
  person: BoardPerson & Record<string, unknown>;
  brief: {
    who: string;
    why: string;
    learned: string[];
    missing: string[];
    sources: string[];
  };
  timeline: Array<{
    event_id: string;
    source: string;
    kind: string;
    summary: string;
    payload: Record<string, unknown>;
  }>;
};

export async function getBoard(): Promise<Board> {
  const res = await get("/v1/board");
  if (!res.ok) throw new Error(`board ${res.status}`);
  return res.json();
}

export async function getPerson(id: string): Promise<PersonFile> {
  const res = await get(`/v1/people/${encodeURIComponent(id)}`);
  if (!res.ok) throw new Error(`person ${res.status}`);
  return res.json();
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

export async function getConversation(): Promise<Conversation> {
  const res = await get("/v1/conversation");
  if (!res.ok) throw new Error(`conversation ${res.status}`);
  const body = await res.json();
  return {
    id: body.id,
    title: body.title,
    messages: body.messages,
    events: Array.isArray(body.events) ? body.events.map(parseChatEvent) : [],
    ...(Array.isArray(body.queue) ? { queue: body.queue } : {}),
    ...(typeof body.queue_paused === "boolean"
      ? { queue_paused: body.queue_paused }
      : {}),
  };
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

const SCHEDULE_RUN_STATUSES = new Set<ScheduleRunStatus>([
  "running",
  "success",
  "failed",
  "waiting_approval",
  "partial",
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
  const status = SCHEDULE_RUN_STATUSES.has(rawStatus) ? rawStatus : "failed";
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
};

export async function getSettings(): Promise<Settings> {
  const res = await get("/v1/settings");
  if (!res.ok) throw new Error(`settings ${res.status}`);
  return res.json();
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

export async function resolveInbox(id: string, decision: "allow" | "deny"): Promise<void> {
  const res = await fetch(`${httpBase()}/v1/inbox/${id}`, {
    method: "POST",
    headers: { "X-Club-Token": apiToken(), "Content-Type": "application/json" },
    body: JSON.stringify({ decision }),
  });
  if (!res.ok) throw new Error(`inbox ${res.status}`);
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

export function openChat(onEvent: (event: SourcecadoSocketEvent) => void): {
  send: (text: string, sessionId: string) => void;
  cancel: (sessionId: string, runId: string) => void;
  queue: (command: QueueCommand) => void;
  recover: (command: RecoveryCommand) => void;
  approve: (id: string, decision: "allow" | "deny") => void;
  close: () => void;
} {
  const token = apiToken();
  const protocols = token ? ["club", token] : ["club"];
  const ws = new WebSocket(`${wsBase()}/ws/chat`, protocols);
  ws.onmessage = (ev) => {
    try {
      onEvent(parseSocketEvent(JSON.parse(String(ev.data))));
    } catch {
      onEvent(parseChatEvent(undefined));
    }
  };
  ws.onerror = () => onEvent({ type: "error", message: "socket error" });
  ws.onclose = (ev) => {
    if (ev.code === 1008) onEvent({ type: "error", message: "sidecar rejected the socket (token)" });
  };
  function push(payload: unknown) {
    if (ws.readyState !== WebSocket.OPEN) {
      onEvent({ type: "error", message: "socket not open yet" });
      return;
    }
    ws.send(JSON.stringify(payload));
  }
  return {
    send(text: string, sessionId: string) {
      push({ type: "chat", text, session_id: sessionId });
    },
    cancel(sessionId: string, runId: string) {
      push({ type: "cancel", session_id: sessionId, run_id: runId });
    },
    queue(command: QueueCommand) {
      push(command);
    },
    recover(command: RecoveryCommand) {
      push(command);
    },
    approve(id: string, decision: "allow" | "deny") {
      push({ type: "permission", id, decision });
    },
    close() {
      ws.close();
    },
  };
}
