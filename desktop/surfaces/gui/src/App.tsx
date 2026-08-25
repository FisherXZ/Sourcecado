import { FormEvent, useEffect, useRef, useState } from "react";
import {
  connectGmail,
  createSession,
  disconnectGmail,
  connectCalendar,
  connectDrive,
  connectGranola,
  getConnectors,
  getGmail,
  getHealth,
  getInbox,
  getPersona,
  getSchedule,
  runScheduleJob,
  getSession,
  getSessions,
  getSettings,
  hasToken,
  openChat,
  renameSession,
  resolveInbox,
  setPersona as postPersona,
  type ChatEvent,
  type Connector,
  type GmailStatus,
  type Health,
  type InboxItem,
  type PersonaInfo,
  type Schedule,
  type SessionRow,
  type Settings,
  type StoredMessage,
} from "./api";

type Item =
  | { kind: "user"; id: number; text: string }
  | { kind: "assistant"; id: number; text: string; live?: boolean }
  | {
      kind: "tool";
      id: string;
      name: string;
      arguments: Record<string, unknown>;
      result?: Record<string, unknown>;
      ok?: boolean;
      live?: boolean;
    }
  | {
      kind: "approval";
      id: string;
      name: string;
      arguments: Record<string, unknown>;
      reason: string;
      resolved?: "allow" | "deny";
    };

export function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [persona, setPersona] = useState<PersonaInfo | null>(null);
  const [schedule, setSchedule] = useState<Schedule | null>(null);
  const [gmail, setGmail] = useState<GmailStatus | null>(null);
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [inbox, setInbox] = useState<InboxItem[]>([]);
  const [sessions, setSessions] = useState<SessionRow[]>([]);
  const [sessionId, setSessionId] = useState<string>("");
  const [bootError, setBootError] = useState<string | null>(null);
  const [items, setItems] = useState<Item[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [runBusy, setRunBusy] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);
  const idRef = useRef(0);
  const chatRef = useRef<ReturnType<typeof openChat> | null>(null);
  const liveId = useRef<number | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!hasToken()) {
      setBootError("missing launch token");
      return;
    }
    Promise.all([
      getHealth(),
      getSessions(),
      getPersona(),
      getSchedule(),
      getGmail(),
      getConnectors(),
      getSettings(),
      getInbox(),
    ]).then(async ([nextHealth, listing, nextPersona, nextSchedule, nextGmail, nextConnectors, nextSettings, nextInbox]) => {
        setHealth(nextHealth);
        setPersona(nextPersona);
        setSchedule(nextSchedule);
        setGmail(nextGmail);
        setConnectors(nextConnectors.connectors);
        setSettings(nextSettings);
        setInbox(nextInbox.items);
        setSessions(listing.sessions);
        const openId = listing.open_id || listing.sessions[0]?.session_id;
        if (openId) {
          setSessionId(openId);
          const conv = await getSession(openId);
          const restored = itemsFromMessages(conv.messages);
          idRef.current = restored.length;
          setItems(restored);
        }
      })
      .catch((err: unknown) => setBootError(err instanceof Error ? err.message : String(err)));
  }, []);

  useEffect(() => {
    function onFocus() {
      getGmail().then(setGmail).catch(() => {});
      getConnectors()
        .then((body) => setConnectors(body.connectors))
        .catch(() => {});
      getInbox()
        .then((body) => setInbox(body.items))
        .catch(() => {});
      getSettings().then(setSettings).catch(() => {});
    }
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, []);

  useEffect(() => {
    if (!hasToken()) return;
    const chat = openChat((event: ChatEvent) => {
      if (event.type === "turn_start") {
        liveId.current = null;
        return;
      }
      if (event.type === "assistant_delta") {
        if (liveId.current == null) {
          const id = ++idRef.current;
          liveId.current = id;
          setItems((m) => [...m, { kind: "assistant", id, text: event.delta, live: true }]);
          return;
        }
        const id = liveId.current;
        setItems((m) =>
          m.map((row) =>
            row.kind === "assistant" && row.id === id ? { ...row, text: row.text + event.delta } : row
          )
        );
        return;
      }
      if (event.type === "permission_required") {
        liveId.current = null;
        setItems((m) => [
          ...m,
          {
            kind: "approval",
            id: event.id,
            name: event.name,
            arguments: event.arguments,
            reason: event.reason,
          },
        ]);
        return;
      }
      if (event.type === "tool_started") {
        liveId.current = null;
        setItems((m) => [
          ...m,
          {
            kind: "tool",
            id: event.id,
            name: event.name,
            arguments: event.arguments,
            live: true,
          },
        ]);
        return;
      }
      if (event.type === "tool_finished") {
        setItems((m) =>
          m.map((row) => {
            if (row.kind === "tool" && row.id === event.id) {
              return { ...row, live: false, ok: event.ok, result: event.result };
            }
            if (
              row.kind === "approval" &&
              row.id === event.id &&
              !row.resolved &&
              event.ok === false
            ) {
              return { ...row, resolved: "deny" as const };
            }
            return row;
          })
        );
        return;
      }
      if (event.type === "turn_end") {
        const id = liveId.current;
        liveId.current = null;
        setBusy(false);
        if (id == null) return;
        setItems((m) =>
          m.map((row) =>
            row.kind === "assistant" && row.id === id
              ? { ...row, live: false, text: event.text || row.text }
              : row
          )
        );
        return;
      }
      if (event.type === "error") {
        liveId.current = null;
        setBusy(false);
        setBanner(event.message);
        setItems((m) =>
          m.map((row) =>
            row.kind === "tool" && row.live
              ? { ...row, live: false, ok: false, result: { error: event.message } }
              : row
          )
        );
      }
    });
    chatRef.current = chat;
    return () => chat.close();
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [items]);

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    const text = draft.trim();
    if (!text || busy) return;
    setBanner(null);
    setDraft("");
    setBusy(true);
    const id = ++idRef.current;
    setItems((m) => [...m, { kind: "user", id, text }]);
    if (sessionId) chatRef.current?.send(text, sessionId);
  }

  async function onNewChat() {
    const created = await createSession();
    setSessionId(created.id);
    setItems([]);
    const listing = await getSessions();
    setSessions(listing.sessions);
  }

  async function onOpenSession(id: string) {
    const conv = await getSession(id);
    setSessionId(id);
    const restored = itemsFromMessages(conv.messages);
    idRef.current = restored.length;
    setItems(restored);
  }

  async function onRename(id: string) {
    const current = sessions.find((row) => row.session_id === id);
    const next = window.prompt("Rename chat", current?.title || "");
    if (!next || !next.trim()) return;
    await renameSession(id, next.trim());
    const listing = await getSessions();
    setSessions(listing.sessions);
  }

  function onApprove(id: string, decision: "allow" | "deny") {
    setItems((m) =>
      m.map((row) =>
        row.kind === "approval" && row.id === id ? { ...row, resolved: decision } : row
      )
    );
    chatRef.current?.approve(id, decision);
  }

  return (
    <main className="app">
      <aside className="rail">
        <p className="eyebrow">Sourcecado</p>
        <button type="button" className="strip-btn" onClick={() => onNewChat().catch((err: unknown) => setBanner(err instanceof Error ? err.message : String(err)))}>
          New chat
        </button>
        <nav className="session-list">
          {sessions.map((row) => (
            <button
              type="button"
              key={row.session_id}
              className={`session-row ${row.session_id === sessionId ? "active" : ""}`}
              onClick={() => onOpenSession(row.session_id).catch((err: unknown) => setBanner(err instanceof Error ? err.message : String(err)))}
              onDoubleClick={() => onRename(row.session_id).catch((err: unknown) => setBanner(err instanceof Error ? err.message : String(err)))}
            >
              {row.title || "New session"}
            </button>
          ))}
        </nav>
      </aside>
      <div className="main">
      <header className="top">
        <div>
          <p className="eyebrow">{persona ? persona.name : "Sourcecado"}</p>
          <h1>Sourcecado</h1>
        </div>
        <p className="model">
          {bootError
            ? bootError
            : health
              ? health.model
                ? health.model
                : "no model key"
              : "connecting…"}
        </p>
      </header>

      <section className="transcript" aria-live="polite">
        {items.length === 0 && (
          <p className="empty">
            {persona?.id === "sourcing"
              ? "Sourcing on duty. Shortlists, drafts for review, why-now."
              : "Generic buddy on duty. Ask, remember, draft (never send)."}
          </p>
        )}
        {items.map((item) =>
          item.kind === "tool" ? (
            <article key={item.id} className={`tool-card ${item.live ? "live" : ""}`}>
              <p className="tool-name">{item.name}</p>
              <p className="tool-result">
                {item.live
                  ? "running…"
                  : item.ok
                    ? formatResult(item.result)
                    : "failed"}
              </p>
            </article>
          ) : item.kind === "approval" ? (
            <article key={item.id} className={`approval-card ${item.resolved ? "resolved" : "pending"}`}>
              <p className="tool-name">{item.name}</p>
              <p className="tool-result">{formatArgs(item.arguments)}</p>
              <p className="approval-reason">{item.reason}</p>
              {item.resolved ? (
                <p className="approval-resolved">{item.resolved === "allow" ? "allowed" : "denied"}</p>
              ) : (
                <div className="approval-actions">
                  <button type="button" className="deny" onClick={() => onApprove(item.id, "deny")}>
                    Deny
                  </button>
                  <button type="button" className="allow" onClick={() => onApprove(item.id, "allow")}>
                    Allow
                  </button>
                </div>
              )}
            </article>
          ) : (
            <article key={item.id} className={`bubble ${item.kind}`}>
              <p>{item.text || (item.kind === "assistant" && item.live ? "…" : "")}</p>
            </article>
          )
        )}
        <div ref={bottomRef} />
      </section>

      <aside className="connector-strip">
        <p>
          {settings ? settings.persona.name : persona?.name} ·{" "}
          {health?.model || "no model"} · Apollo{" "}
          {settings?.apollo.configured ? "on" : "off"}
        </p>
        <button
          type="button"
          className="strip-btn"
          onClick={() =>
            postPersona(persona?.id === "sourcing" ? "buddy" : "sourcing")
              .then((body) => {
                setPersona({ id: body.persona.id, name: body.persona.name, tools: persona?.tools || [] });
                setSettings((s) => (s ? { ...s, persona: body.persona } : s));
              })
              .catch((err: unknown) => setBanner(err instanceof Error ? err.message : String(err)))
          }
        >
          {persona?.id === "sourcing" ? "Use buddy" : "Use sourcing"}
        </button>
      </aside>

      <aside className="connector-strip">
        <p>
          {connectors.length
            ? connectors.map((c) => `${c.title} · ${c.status}${c.email ? ` ${c.email}` : ""}`).join(" · ")
            : gmail?.connected
              ? `Gmail · ${gmail.email || "connected"}`
              : "Gmail · not connected"}
        </p>
        {gmail?.connected ? (
          <button
            type="button"
            className="strip-btn"
            onClick={() =>
              disconnectGmail()
                .then((status) =>
                  getConnectors().then((next) => {
                    setGmail(status);
                    setConnectors(next.connectors);
                  })
                )
                .catch((err: unknown) => setBanner(err instanceof Error ? err.message : String(err)))
            }
          >
            Disconnect Google
          </button>
        ) : (
          <button
            type="button"
            className="strip-btn"
            onClick={() =>
              connectGmail()
                .then((body) => {
                  if (!body.opened && body.url) {
                    window.open(body.url, "_blank", "noopener,noreferrer");
                  }
                  if (body.redirect_uri) {
                    setBanner(
                      `Google must allow this redirect: ${body.redirect_uri}. If the page errors, add that URI on the OAuth client, then click Connect Gmail again.`
                    );
                  }
                })
                .catch((err: unknown) => setBanner(err instanceof Error ? err.message : String(err)))
            }
          >
            Connect Gmail
          </button>
        )}
        <button
          type="button"
          className="strip-btn"
          onClick={() =>
            connectDrive()
              .then((body) => {
                if (!body.opened && body.url) window.open(body.url, "_blank", "noopener,noreferrer");
              })
              .catch((err: unknown) => setBanner(err instanceof Error ? err.message : String(err)))
          }
        >
          Connect Drive
        </button>
        <button
          type="button"
          className="strip-btn"
          onClick={() =>
            connectCalendar()
              .then((body) => {
                if (!body.opened && body.url) window.open(body.url, "_blank", "noopener,noreferrer");
              })
              .catch((err: unknown) => setBanner(err instanceof Error ? err.message : String(err)))
          }
        >
          Connect Calendar
        </button>
        <button
          type="button"
          className="strip-btn"
          onClick={() =>
            connectGranola()
              .then((body) => {
                if (body.url) window.open(body.url, "_blank", "noopener,noreferrer");
              })
              .catch((err: unknown) => setBanner(err instanceof Error ? err.message : String(err)))
          }
        >
          Connect Granola
        </button>
      </aside>

      {inbox.map((item) => (
        <aside key={item.id} className="connector-strip">
          <p>
            inbox · {item.name}
            {Object.keys(item.arguments || {}).length ? ` · ${formatArgs(item.arguments)}` : ""}
          </p>
          <span>
            <button
              type="button"
              className="strip-btn"
              onClick={() =>
                resolveInbox(item.id, "deny")
                  .then(() => getInbox().then((body) => setInbox(body.items)))
                  .catch((err: unknown) => setBanner(err instanceof Error ? err.message : String(err)))
              }
            >
              Deny
            </button>
            <button
              type="button"
              className="strip-btn"
              onClick={() =>
                resolveInbox(item.id, "allow")
                  .then(() => getInbox().then((body) => setInbox(body.items)))
                  .catch((err: unknown) => setBanner(err instanceof Error ? err.message : String(err)))
              }
            >
              Allow
            </button>
          </span>
        </aside>
      ))}

      {schedule && schedule.jobs.length > 0 && (
        <aside className="connector-strip">
          <p>
            <strong>{schedule.jobs[0].cron}</strong> · {schedule.jobs[0].prompt}
            {schedule.jobs[0].next_run_at ? ` · next ${schedule.jobs[0].next_run_at}` : ""}
          </p>
          <p>
            {runBusy
              ? "running…"
              : schedule.runs.length
                ? `last run · ${schedule.runs[schedule.runs.length - 1].status}`
                : "no runs yet"}
          </p>
          <button
            type="button"
            className="strip-btn"
            disabled={runBusy}
            onClick={() => {
              if (runBusy) return;
              setRunBusy(true);
              setBanner(null);
              runScheduleJob(schedule.jobs[0].id)
                .then(() =>
                  Promise.all([getSchedule(), getInbox()]).then(([nextSchedule, nextInbox]) => {
                    setSchedule(nextSchedule);
                    setInbox(nextInbox.items);
                  })
                )
                .catch((err: unknown) => {
                  const msg = err instanceof Error ? err.message : String(err);
                  setBanner(msg === "already running" ? "That job is still running. Wait for last run to update." : msg);
                })
                .finally(() => setRunBusy(false));
            }}
          >
            {runBusy ? "Running…" : "Run now"}
          </button>
          {schedule.runs.length > 0 && (
            <button
              type="button"
              className="strip-btn"
              onClick={() =>
                getSession(`sched-${schedule.jobs[0].id}`)
                  .then((conv) => {
                    setSessionId(conv.id);
                    const restored = itemsFromMessages(conv.messages);
                    idRef.current = restored.length;
                    setItems(restored);
                  })
                  .catch((err: unknown) => setBanner(err instanceof Error ? err.message : String(err)))
              }
            >
              Open last run
            </button>
          )}
        </aside>
      )}

      {banner && <p className="status warn">{banner}</p>}

      <form className="composer" onSubmit={onSubmit}>
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              onSubmit(e);
            }
          }}
          placeholder="Ask Club"
          rows={2}
          disabled={busy}
        />
        <button type="submit" disabled={busy || !draft.trim()}>
          Send
        </button>
      </form>
      </div>
    </main>
  );
}

function formatResult(result: Record<string, unknown> | undefined): string {
  if (!result) return "done";
  if (typeof result.iso === "string") return result.iso;
  if (result.sent === true && typeof result.message === "string") {
    return `sent · ${result.message}`;
  }
  if (result.saved === true) return `remembered #${result.id}`;
  if (result.updated === true) return `updated #${result.id}`;
  if (result.forgotten === true) return `forgot #${result.id}`;
  if (result.drafted === true) return `draft ${result.id} · not sent`;
  if (Array.isArray(result.people)) return `${result.people.length} people`;
  return JSON.stringify(result);
}

function formatArgs(args: Record<string, unknown>): string {
  const to = args.to;
  const subject = args.subject;
  if (typeof to === "string" && to) {
    return typeof subject === "string" && subject ? `${to} · ${subject}` : to;
  }
  const message = args.message;
  if (typeof message === "string" && message) return message;
  const content = args.content;
  if (typeof content === "string" && content) {
    const mid = args.memory_id;
    return mid != null ? `#${mid} · ${content}` : content;
  }
  if (args.memory_id != null) return `#${args.memory_id}`;
  return JSON.stringify(args);
}

function itemsFromMessages(messages: StoredMessage[]): Item[] {
  const items: Item[] = [];
  let n = 0;
  for (const msg of messages) {
    if (msg.role === "user" && typeof msg.content === "string") {
      items.push({ kind: "user", id: ++n, text: msg.content });
      continue;
    }
    if (msg.role === "assistant") {
      const text = typeof msg.content === "string" ? msg.content : "";
      if (text) items.push({ kind: "assistant", id: ++n, text });
      continue;
    }
    if (msg.role === "tool") {
      let result: Record<string, unknown> = {};
      try {
        result = JSON.parse(String(msg.content || "{}")) as Record<string, unknown>;
      } catch {
        result = { error: String(msg.content || "") };
      }
      items.push({
        kind: "tool",
        id: `restored-${msg.tool_call_id || n}`,
        name: msg.name || "tool",
        arguments: {},
        result,
        ok: !result.error,
      });
    }
  }
  return items;
}
