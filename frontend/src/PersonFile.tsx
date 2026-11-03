import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";

import { DescriptionEditor } from "./DescriptionEditor";
import { PersonDetailsEditor, type PersonDetailsHandle } from "./crm/PersonDetailsEditor";
import { FollowUpPanel } from "./FollowUpPanel";
import { EnrichmentPanel } from "./EnrichmentPanel";
import { OutreachPanel } from "./OutreachPanel";
import { MonidResearch } from "./MonidResearch";
import { TaskList } from "./Tasks";
import { MailActivity } from "./crm/MailActivity";
import { SavedTaskDrafts, savedTaskDrafts } from "./crm/SavedTaskDrafts";
import { SequencePill } from "./crm/SequencePill";
import { Glyph, type GlyphName } from "./generative/ResultCard";
import { Icon } from "./icons";
import {
  Artifacts,
  BriefSummary,
  LearnedAndConflicts,
  SourceReferences,
  Timeline,
  VersionHistory,
} from "./person/PersonBrief";
import { DriveEvidence, MeetingEvidence } from "./person/PersonEvidence";
import { NextTasks, useOpenTasks } from "./person/NextTasks";
import { SuccessorHandoff } from "./person/SuccessorHandoff";

import {
  defaultPersonDescriptions,
  getPerson,
  newOperationId,
  openPersonSourcingChat,
  refreshReplies,
  setPersonSequence,
  undoTaskChange,
  type PersonFile,
} from "./api";

const TABS = [
  { id: "overview", label: "Overview", icon: "user" },
  { id: "emails", label: "Emails", icon: "mail" },
  { id: "tasks", label: "Tasks", icon: "review" },
  { id: "notes", label: "Notes", icon: "doc" },
  { id: "research", label: "Research", icon: "inspect" },
  { id: "history", label: "History", icon: "clock" },
] as const satisfies ReadonlyArray<{ id: string; label: string; icon: GlyphName }>;

type TabId = (typeof TABS)[number]["id"];

function displayName(file: PersonFile): string {
  const { first_name, last_name } = file.person;
  return (
    [first_name, last_name].filter(Boolean).join(" ") ||
    (file.brief.who ?? "").split(",")[0]?.trim() ||
    "Person"
  );
}

function subtitle(file: PersonFile): string {
  const line = [file.person.title, file.person.company].filter(Boolean).join(" · ");
  if (line) return line;
  // Older people carry role and company only inside the brief's "who" line.
  const who = file.brief.who ?? "";
  const comma = who.indexOf(",");
  return comma > 0 ? who.slice(comma + 1).trim() : "";
}

function initials(name: string): string {
  return (
    name
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((word) => word[0]?.toUpperCase() ?? "")
      .join("") || "?"
  );
}

function emailCount(file: PersonFile): number {
  return file.timeline.filter(
    (event) =>
      (event.kind === "send" && event.payload.sent) ||
      event.payload.direction === "inbound" ||
      event.payload.direction === "outbound",
  ).length;
}

function TabPanel({ id, active, children }: { id: TabId; active: boolean; children: ReactNode }) {
  // Every panel stays mounted so unsaved edits survive a tab switch.
  return (
    <div
      role="tabpanel"
      id={`pf-panel-${id}`}
      aria-labelledby={`pf-tab-${id}`}
      tabIndex={0}
      className={`pf-panel${active ? "" : " is-inactive"}`}
    >
      {children}
    </div>
  );
}

export function PersonFileView({
  personId,
  followUpTaskId,
}: {
  personId: string;
  followUpTaskId?: string;
}) {
  const [file, setFile] = useState<PersonFile | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [moving, setMoving] = useState<string | null>(null);
  const [moveFailed, setMoveFailed] = useState<string | null>(null);
  const [undoChange, setUndoChange] = useState<string | null>(null);
  const [openingChat, setOpeningChat] = useState(false);
  const [chatFailed, setChatFailed] = useState(false);
  const [mailBusy, setMailBusy] = useState(false);
  const [mailStatus, setMailStatus] = useState<string | null>(null);
  const [tab, setTab] = useState<TabId>(followUpTaskId ? "tasks" : "overview");
  const details = useRef<PersonDetailsHandle>(null);
  const editButton = useRef<HTMLButtonElement>(null);
  const openTasks = useOpenTasks(personId);

  useEffect(() => {
    let active = true;
    setFailed(false);
    // A refresh of the same person keeps the page mounted, so an open task
    // editor keeps its unsaved text. Switching person starts clean.
    setFile((current) => (current?.person.person_id === personId ? current : null));
    getPerson(personId).then(
      (next) => {
        if (active) setFile(next);
      },
      () => {
        if (active) setFailed(true);
      },
    );
    return () => {
      active = false;
    };
  }, [attempt, personId]);

  useEffect(() => {
    setMailStatus(null);
    setUndoChange(null);
    setMoveFailed(null);
    setTab(followUpTaskId ? "tasks" : "overview");
  }, [personId]);

  useEffect(() => {
    if (followUpTaskId) setTab("tasks");
  }, [followUpTaskId]);

  useEffect(() => {
    const refresh = () => setAttempt((value) => value + 1);
    window.addEventListener("sourcecado:board-changed", refresh);
    return () => window.removeEventListener("sourcecado:board-changed", refresh);
  }, []);

  async function reload() {
    setFile(await getPerson(personId));
  }

  async function refreshMail() {
    if (mailBusy) return;
    setMailBusy(true);
    setMailStatus(null);
    try {
      const { refresh } = await refreshReplies();
      setMailStatus(refresh.status === "failed" ? "Gmail check failed. Saved activity is kept."
        : refresh.status === "partial" ? "Collection is unfinished. Refresh again to continue."
          : "Gmail activity refreshed.");
      setAttempt(value => value + 1);
    } catch {
      setMailStatus("Couldn’t check Gmail. Saved activity is kept. Try again.");
    } finally {
      setMailBusy(false);
    }
  }

  async function move(state: string) {
    if (moving) return;
    setMoving(state);
    setMoveFailed(null);
    try {
      // The server also returns the recorded change, which Undo reverses.
      const result = (await setPersonSequence(personId, state)) as {
        change?: { change_id?: string };
      };
      setUndoChange(result.change?.change_id ?? null);
      setFile(await getPerson(personId));
      // Tell any open view listening for this signal (Contacts, and this page
      // itself) to refetch, so its status and filter counts reflect the change
      // without a reload. Only fires after a successful write; a failure falls
      // through to catch and leaves the previous state in place. Mirrors the
      // board-changed event ChatPage already dispatches for the same purpose.
      window.dispatchEvent(new CustomEvent("sourcecado:board-changed"));
    } catch {
      setMoveFailed("Couldn’t update the sequence state. Try again.");
    } finally {
      setMoving(null);
    }
  }

  async function undoMove() {
    if (moving || !undoChange) return;
    setMoving("undo");
    setMoveFailed(null);
    try {
      await undoTaskChange(personId, undoChange, { operationId: newOperationId() });
      setUndoChange(null);
      setFile(await getPerson(personId));
      window.dispatchEvent(new CustomEvent("sourcecado:board-changed"));
    } catch {
      setUndoChange(null);
      setMoveFailed("Couldn’t undo. The sequence state changed again.");
    } finally {
      setMoving(null);
    }
  }

  async function openSourcingChat() {
    if (openingChat || !file) return;
    const version = file.person.version;
    if (typeof version !== "number" || !Number.isInteger(version)) {
      setChatFailed(true);
      return;
    }
    setOpeningChat(true);
    setChatFailed(false);
    try {
      const result = await openPersonSourcingChat(personId, version);
      if (result.active_person.person_id !== personId) {
        throw new Error("person binding mismatch");
      }
      window.location.hash = `#/chat/${encodeURIComponent(result.session.id)}/person/${encodeURIComponent(personId)}`;
    } catch {
      setChatFailed(true);
    } finally {
      setOpeningChat(false);
    }
  }

  function onTabKey(event: KeyboardEvent<HTMLDivElement>) {
    const index = TABS.findIndex((item) => item.id === tab);
    const last = TABS.length - 1;
    const next =
      event.key === "ArrowRight" ? (index === last ? 0 : index + 1)
        : event.key === "ArrowLeft" ? (index === 0 ? last : index - 1)
          : event.key === "Home" ? 0
            : event.key === "End" ? last
              : -1;
    if (next < 0) return;
    event.preventDefault();
    setTab(TABS[next].id);
    document.getElementById(`pf-tab-${TABS[next].id}`)?.focus();
  }

  if (failed && !file) {
    return (
      <main className="route-page person-page pf">
        <h1>Person</h1>
        <section className="route-error" role="alert">
          <p>Couldn’t load this person file.</p>
          <button type="button" onClick={() => setAttempt((value) => value + 1)}>
            <Icon name="refresh" />
            Retry
          </button>
          <a href="#/board">Back to Contacts</a>
        </section>
      </main>
    );
  }

  if (!file) {
    return (
      <main className="route-page person-page pf" aria-busy="true">
        <h1>Person</h1>
        <p role="status">Loading person file…</p>
      </main>
    );
  }

  const name = displayName(file);
  const role = subtitle(file);
  // While a change is saving, the pill already shows where it is going.
  const sequence = moving && moving !== "undo" ? moving : (file.person.sequence_state ?? "");
  const descriptions = file.descriptions ?? defaultPersonDescriptions();
  const reloadQuietly = () => {
    void getPerson(personId).then(setFile);
  };
  const attention = [
    ...new Set(
      [
        file.person.attention_reason,
        ...(file.person.attention_reasons ?? []).map((reason) => reason.label),
      ].filter((text): text is string => typeof text === "string" && text.trim() !== ""),
    ),
  ];
  const counts: Partial<Record<TabId, number>> = {
    emails: emailCount(file),
    ...(openTasks ? { tasks: openTasks.length } : {}),
  };

  return (
    <main className="route-page person-page pf">
      <header className="pf-header">
        <a className="pf-back" href="#/board" aria-label="Back to Contacts">
          <Icon name="back" />
          Contacts
        </a>
        <div className="pf-identity">
          <span className="pf-avatar" aria-hidden="true">
            {initials(name)}
          </span>
          <div className="pf-title">
            <h1>{name}</h1>
            {role || file.person.last_name_status === "hidden_by_apollo" ? (
              <p className="pf-subtitle">
                {role ? <span>{role}</span> : null}
                {file.person.last_name_status === "hidden_by_apollo" ? (
                  <span className="pf-badge is-warn" title="Enrich to verify the full name.">
                    Surname hidden by Apollo
                  </span>
                ) : null}
              </p>
            ) : null}
          </div>
          <div className="pf-actions">
            <SequencePill
              label="Sequence state"
              value={sequence}
              disabled={moving !== null}
              onChange={(state) => void move(state)}
              onUndo={undoChange ? () => void undoMove() : undefined}
            />
            <button
              type="button"
              className="pf-btn pf-btn-primary"
              disabled={openingChat}
              onClick={() => void openSourcingChat()}
            >
              <Icon name="chat" />
              {openingChat ? "Opening chat…" : file.sourcing_chat ? "Open chat" : "Start chat"}
            </button>
            <button
              ref={editButton}
              type="button"
              className="pf-btn"
              onClick={() => details.current?.begin()}
            >
              <Icon name="edit" />
              Edit details
            </button>
          </div>
        </div>
        {chatFailed ? (
          <p className="pf-alert" role="alert">
            Couldn’t open this person’s sourcing chat. Refresh the person file and try again.
          </p>
        ) : null}
        {moveFailed ? (
          <p className="pf-alert" role="alert">
            {moveFailed}
          </p>
        ) : null}
      </header>

      {failed ? <p className="task-error" role="alert">Couldn’t refresh this person file. Previously loaded work is kept.</p> : null}
      {file.brief.partial ? (
        <p className="person-partial" role="status">
          Partial brief. {file.brief.partial_sources.join(", ")} could not be refreshed.
        </p>
      ) : null}

      <div className="pf-layout">
        <div className="pf-sidebar">
          <PersonDetailsEditor
            key={personId}
            ref={details}
            editTrigger={editButton}
            person={file.person}
            history={file.crm_changes}
            onSaved={person => setFile(current => current?.person.person_id === person.person_id ? { ...current, person } : current)}
          />
          <EnrichmentPanel
            personId={personId}
            onEditDetail={(field) => details.current?.begin(field)}
            // The first lookup review opens this person's sourcing chat; read
            // the file again so the header and Outreach see that chat.
            onSessionOpened={reloadQuietly}
          />
        </div>

        <div className="pf-main">
          <div className="pf-tabs" role="tablist" aria-label="Person file sections" onKeyDown={onTabKey}>
            {TABS.map((item) => {
              const selected = item.id === tab;
              const count = counts[item.id];
              return (
                <button
                  key={item.id}
                  type="button"
                  role="tab"
                  id={`pf-tab-${item.id}`}
                  aria-selected={selected}
                  aria-controls={`pf-panel-${item.id}`}
                  tabIndex={selected ? 0 : -1}
                  onClick={() => setTab(item.id)}
                >
                  <Glyph name={item.icon} />
                  {item.label}
                  {count ? <span className="pf-tab-count">{count}</span> : null}
                </button>
              );
            })}
          </div>

          <TabPanel id="overview" active={tab === "overview"}>
            {file.brief.why || attention.length > 0 ? (
              <div className="pf-glance">
                {attention.length > 0 ? (
                  <p className="pf-attention-flag">
                    <Glyph name="info" />
                    {attention.join(" · ")}
                  </p>
                ) : null}
                {file.brief.why ? (
                  <p className="pf-why">
                    <Glyph name="keep" />
                    {file.brief.why}
                  </p>
                ) : null}
              </div>
            ) : null}
            <section className="person-section" aria-labelledby="person-description-heading">
              <h2 id="person-description-heading" tabIndex={-1}>Description</h2>
              <DescriptionEditor
                personId={personId}
                description={descriptions.general}
                onSaved={reloadQuietly}
                rows={2}
              />
            </section>
            <NextTasks tasks={openTasks} onShowAll={() => setTab("tasks")} />
            <OutreachPanel
              personId={personId}
              sessionId={file.sourcing_chat?.session_id ?? null}
              personVersion={typeof file.person.version === "number" ? file.person.version : null}
              recipient={typeof file.person.email === "string" ? file.person.email : null}
              prepared={savedTaskDrafts(file)[0]}
              // Draft email opened the chat, so the header now offers Open chat.
              onChatOpened={() => setAttempt((value) => value + 1)}
            />
            <SavedTaskDrafts file={file} />
          </TabPanel>

          <TabPanel id="emails" active={tab === "emails"}>
            <MailActivity events={file.timeline} gaps={file.person.knowledge_gaps ?? []}
              sync={file.mail_sync} analysis={file.mail_analysis}
              followUp={file.person.follow_up} onRefresh={() => void refreshMail()}
              refreshing={mailBusy} refreshStatus={mailStatus} />
          </TabPanel>

          <TabPanel id="tasks" active={tab === "tasks"}>
            {followUpTaskId ? (
              <FollowUpPanel
                key={followUpTaskId}
                personId={personId}
                taskId={followUpTaskId}
                personVersion={typeof file.person.version === "number" ? file.person.version : null}
                sessionId={file.sourcing_chat?.session_id ?? null}
                onClose={() => {
                  window.location.hash = `#/people/${encodeURIComponent(personId)}`;
                  // Put focus back on the task's Prepare draft link.
                  requestAnimationFrame(() =>
                    document.getElementById(`task-prepare-${followUpTaskId}`)?.focus(),
                  );
                }}
              />
            ) : null}
            <div className="person-tasks" id="person-tasks" tabIndex={-1}>
              <TaskList personId={personId} heading="Tasks" />
            </div>
          </TabPanel>

          <TabPanel id="notes" active={tab === "notes"}>
            <section className="person-section" aria-labelledby="person-detailed-description-heading">
              <h2 id="person-detailed-description-heading">Detailed description</h2>
              <DescriptionEditor
                personId={personId}
                description={descriptions.detailed}
                onSaved={reloadQuietly}
                rows={6}
              />
            </section>
            <SuccessorHandoff personId={personId} file={file} onSaved={setFile} />
          </TabPanel>

          <TabPanel id="research" active={tab === "research"}>
            <MonidResearch personId={personId} sessionId={file.sourcing_chat?.session_id ?? null} />
            <MeetingEvidence personId={personId} evidence={file.meeting_evidence} onChanged={reload} />
            <DriveEvidence personId={personId} sources={file.person.sources ?? []} onChanged={reload} />
            <SourceReferences brief={file.brief} />
            <Artifacts brief={file.brief} />
          </TabPanel>

          <TabPanel id="history" active={tab === "history"}>
            <BriefSummary brief={file.brief} />
            <LearnedAndConflicts brief={file.brief} />
            <Timeline events={file.timeline} />
            <VersionHistory
              personId={personId}
              file={file}
              onReverted={() => setAttempt((value) => value + 1)}
            />
          </TabPanel>
        </div>
      </div>
    </main>
  );
}
