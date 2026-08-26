declare const __CLUB_DEV_TOKEN__: string;

export type Health = {
  status: string;
  piece: string;
  slice: number;
  model: string | null;
  persona?: string;
};

export type PersonaInfo = { id: string; name: string; tools: string[] };

export type Schedule = {
  jobs: Array<{ id: number; cron: string; prompt: string; created_at: string; next_run_at?: string | null }>;
  runs: Array<{ id: number; job_id: number; status: string; result: string | null; created_at: string }>;
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
};

export type ChatEvent =
  | { type: "turn_start" }
  | { type: "assistant_delta"; delta: string }
  | { type: "permission_required"; id: string; name: string; arguments: Record<string, unknown>; reason: string }
  | { type: "tool_started"; id: string; name: string; arguments: Record<string, unknown> }
  | { type: "tool_finished"; id: string; name: string; ok: boolean; result: Record<string, unknown> }
  | { type: "turn_end"; text: string }
  | { type: "error"; message: string };

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
  updated_at: string;
};

export async function getSessions(): Promise<{ sessions: SessionRow[]; open_id: string | null }> {
  const res = await get("/v1/sessions");
  if (!res.ok) throw new Error(`sessions ${res.status}`);
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
  return { id: body.id, title: body.title, messages: body.messages };
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

export async function getConversation(): Promise<Conversation> {
  const res = await get("/v1/conversation");
  if (!res.ok) throw new Error(`conversation ${res.status}`);
  return res.json();
}

export async function getPersona(): Promise<PersonaInfo> {
  const res = await get("/v1/persona");
  if (!res.ok) throw new Error(`persona ${res.status}`);
  return res.json();
}

export async function getSchedule(): Promise<Schedule> {
  const res = await get("/v1/schedule");
  if (!res.ok) throw new Error(`schedule ${res.status}`);
  return res.json();
}

export async function runScheduleJob(id: number): Promise<{ run: { id: number; status: string } }> {
  const res = await fetch(`${httpBase()}/v1/schedule/${id}/run`, {
    method: "POST",
    headers: { "X-Club-Token": apiToken() },
  });
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || `schedule run ${res.status}`);
  return body;
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

export type Connector = {
  id: string;
  title: string;
  status: string;
  email: string | null;
};

export async function getConnectors(): Promise<{ connectors: Connector[] }> {
  const res = await get("/v1/connectors");
  if (!res.ok) throw new Error(`connectors ${res.status}`);
  return res.json();
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

export function openChat(onEvent: (event: ChatEvent) => void): {
  send: (text: string, sessionId: string) => void;
  approve: (id: string, decision: "allow" | "deny") => void;
  close: () => void;
} {
  const token = apiToken();
  const protocols = token ? ["club", token] : ["club"];
  const ws = new WebSocket(`${wsBase()}/ws/chat`, protocols);
  ws.onmessage = (ev) => {
    try {
      onEvent(JSON.parse(String(ev.data)) as ChatEvent);
    } catch {
      onEvent({ type: "error", message: "bad event from sidecar" });
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
    approve(id: string, decision: "allow" | "deny") {
      push({ type: "permission", id, decision });
    },
    close() {
      ws.close();
    },
  };
}
