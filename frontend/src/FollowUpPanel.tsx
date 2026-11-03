import { useEffect, useRef, useState } from "react";
import { AfterSendReminder } from "./AfterSendReminder";
import { DraftVersion, SendApproval } from "./crm/SendApproval";
import { UndoToast, useUndoToast } from "./crm/UndoToast";

import {
  createFollowUpDraft,
  decideApproval,
  editOutreachDraft,
  FollowUpError,
  openPersonSourcingChat,
  prepareFollowUp,
  readOutreachDraft,
  requestSendApproval,
  TaskSaveError,
  updateTask,
  type FollowUpPreparation,
  type FollowUpThread,
  type OutreachDraft,
  type PendingSendApproval,
} from "./api";
import { writeDraft } from "./chat/draftStorage";
import {
  clearFollowUpText,
  readFollowUpText,
  writeFollowUpText,
} from "./followUpDrafts";
import { Glyph } from "./generative/ResultCard";
import { Icon } from "./icons";
import { DueChip, stateToast, TASKS_CHANGED, useOperationId } from "./Tasks";

const FAILURE: Record<string, string> = {
  no_recipient: "No email address on file, so no Gmail draft yet. Your text is saved here.",
  thread_unavailable: "Couldn’t read the Gmail thread, so no draft was made. Your text is saved here. Try again.",
  thread_not_preserved:
    "Gmail put this reply in a different thread, so Sourcecado won’t send it. Your text is saved here.",
  thread_not_on_file: "That thread isn’t one of this person’s threads.",
  thread_mismatch: "This draft isn’t in the reviewed thread, so it can’t be approved.",
  stale_draft: "The draft changed in Gmail. The current text is shown. Review it again.",
  recipient_not_bound: "This draft is addressed to someone else.",
  unbound_session: "This person’s chat changed. Reopen the follow-up.",
};

function failure(error: unknown): string {
  if (error instanceof FollowUpError && error.code && FAILURE[error.code]) {
    return FAILURE[error.code];
  }
  return error instanceof Error ? error.message : "Something went wrong.";
}

function threadLine(thread: FollowUpThread): string {
  const last = thread.last_direction === "inbound" ? "last message theirs" : "last message ours";
  const count = `${thread.message_count} message${thread.message_count === 1 ? "" : "s"}`;
  return `${thread.subject || "No subject"} · ${count}, ${last}`;
}

/**
 * Write, edit, review and send a follow-up email for one task.
 *
 * The director can write without an email address. The Gmail draft goes in
 * the thread shown, and edits are saved back to it. The approval covers the
 * exact account, recipient, thread, subject and message shown; any change
 * after that needs a new approval. Sending does not complete the task.
 */
export function FollowUpPanel({
  personId,
  taskId,
  personVersion,
  sessionId: boundSessionId,
  onClose,
}: {
  readonly personId: string;
  readonly taskId: string;
  readonly personVersion: number | null;
  readonly sessionId: string | null;
  readonly onClose: () => void;
}) {
  const [prepared, setPrepared] = useState<FollowUpPreparation | null>(null);
  const [loadFailed, setLoadFailed] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [threadId, setThreadId] = useState<string | null | undefined>(undefined);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [draft, setDraft] = useState<OutreachDraft | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [approval, setApproval] = useState<PendingSendApproval | null>(null);
  const [sent, setSent] = useState<Record<string, unknown> | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(boundSessionId);
  const [taskDone, setTaskDone] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const completion = useOperationId();
  const { toast, show, dismiss } = useUndoToast();

  useEffect(() => setSessionId(boundSessionId), [boundSessionId]);

  useEffect(() => {
    heading.current?.focus();
    heading.current?.scrollIntoView?.({ block: "start" });
  }, [taskId]);

  useEffect(() => {
    let active = true;
    setLoadFailed(null);
    prepareFollowUp(personId, taskId, threadId ?? null).then(
      async (result) => {
        if (!active) return;
        const next = result.follow_up;
        setPrepared(next);
        if (result.sourcing_chat) setSessionId(result.sourcing_chat.session_id);
        const chosen = next.thread?.thread_id ?? null;
        const saved = readFollowUpText(personId, taskId);
        const usable = saved && saved.threadId === chosen ? saved : null;
        setSubject(next.thread ? next.subject : (usable?.subject ?? next.subject));
        setBody(usable?.body ?? next.body);
        if (usable?.draftId) {
          try {
            const live = await readOutreachDraft(personId, usable.draftId);
            if (active) setDraft(live);
          } catch {
            // The draft is gone (sent, deleted or unreachable). Keep the text.
            writeFollowUpText(personId, taskId, { ...usable, draftId: null });
          }
        }
      },
      (error: unknown) => {
        if (active) setLoadFailed(failure(error));
      },
    );
    return () => {
      active = false;
    };
  }, [personId, taskId, threadId, attempt]);

  const thread = prepared?.thread ?? null;
  const replying = thread !== null;

  function remember(next: { subject?: string; body?: string; draftId?: string | null }) {
    writeFollowUpText(personId, taskId, {
      subject: next.subject ?? subject,
      body: next.body ?? body,
      threadId: thread?.thread_id ?? null,
      draftId: next.draftId !== undefined ? next.draftId : (draft?.id ?? null),
    });
  }

  async function run(label: string, work: () => Promise<void>) {
    if (busy) return;
    setBusy(label);
    setProblem(null);
    try {
      await work();
    } catch (error) {
      if (error instanceof FollowUpError && error.code === "stale_draft" && error.draft) {
        setDraft(error.draft);
        setApproval(null);
      }
      setProblem(failure(error));
    } finally {
      setBusy(null);
    }
  }

  async function boundSession(): Promise<string> {
    if (sessionId) return sessionId;
    if (personVersion === null) throw new Error("Reload this person file and try again.");
    const opened = await openPersonSourcingChat(personId, personVersion);
    if (opened.active_person.person_id !== personId) {
      throw new Error("This chat is bound to a different person.");
    }
    setSessionId(opened.session.id);
    return opened.session.id;
  }

  async function askAssistant() {
    if (!prepared) return;
    remember({});
    const opened =
      personVersion === null ? null : await openPersonSourcingChat(personId, personVersion);
    if (!opened || opened.active_person.person_id !== personId) {
      throw new Error("Couldn’t open this person’s sourcing chat.");
    }
    // Fill in the chat box. The director sends it.
    writeDraft(opened.session.id, prepared.assistant_prompt);
    window.location.hash = `#/chat/${encodeURIComponent(opened.session.id)}/person/${encodeURIComponent(personId)}`;
  }

  async function denyPending() {
    if (!approval) return;
    await decideApproval(approval.id, "deny");
    setApproval(null);
  }

  /** Sending leaves the task open; this closes it from the sent card, with Undo. */
  async function completeTask() {
    if (!prepared) return;
    const { version, title } = prepared.task;
    try {
      const result = await updateTask(personId, taskId, {
        changes: { state: "completed" },
        expectedVersion: version,
        operationId: completion.forPayload({ taskId, state: "completed", version }),
      });
      completion.settle();
      setTaskDone(true);
      const undo = stateToast({ person_id: personId, title }, "completed", result?.change);
      show({
        ...undo,
        onUndo: undo.onUndo && (async () => {
          await undo.onUndo?.();
          setTaskDone(false);
        }),
      });
    } catch (error) {
      if (error instanceof TaskSaveError && error.code === "task_conflict") {
        completion.settle();
        throw new Error("This task changed after the follow-up opened. Finish it from Tasks.");
      }
      throw new Error("Couldn’t mark the task done. Try again.");
    } finally {
      window.dispatchEvent(new Event(TASKS_CHANGED));
    }
  }

  const header = (
    <div className="follow-up-header">
      <h2 id="follow-up-heading" ref={heading} tabIndex={-1}>
        Follow-up{prepared ? `: ${prepared.task.title}` : ""}
      </h2>
      {prepared?.task.due_date ? <DueChip date={prepared.task.due_date} /> : null}
      <button
        type="button"
        className="crm-icon-btn follow-up-close"
        aria-label="Close follow-up"
        title="Close"
        onClick={onClose}
      >
        <Icon name="close" />
      </button>
    </div>
  );

  if (loadFailed) {
    return (
      <section className="person-section follow-up" aria-labelledby="follow-up-heading">
        {header}
        <div role="alert" className="crm-alert">
          <Glyph name="info" />
          <span>Couldn’t prepare this follow-up. {loadFailed}</span>
          <button type="button" className="crm-btn" onClick={() => setAttempt((value) => value + 1)}>
            <Icon name="refresh" />
            Retry
          </button>
        </div>
      </section>
    );
  }

  const blockers = prepared?.blockers ?? [];
  const cannotDraft = blockers.some((item) => item.code === "no_recipient" || item.code === "gmail_disconnected");
  const reviewedHere =
    approval !== null && draft !== null && approval.resource.body_digest === draft.body_digest;

  return (
    <section
      className="person-section follow-up"
      aria-labelledby="follow-up-heading"
      aria-busy={prepared === null ? "true" : undefined}
    >
      {header}

      {prepared === null ? (
        <p className="crm-line" role="status">
          <Glyph name="loading" />
          Preparing follow-up…
        </p>
      ) : (
        <>
          {blockers.map((item) => (
            <p key={item.code} className="crm-note">
              <Glyph name="info" />
              {item.message}
            </p>
          ))}

          {sent ? (
            <article className="crm-card crm-sent person-outreach-sent" role="status" aria-label="Sent follow-up">
              <header className="crm-card-head">
                <span className="crm-line is-ok">
                  <Icon name="send" />
                  <strong>Sent</strong>
                </span>
              </header>
              <div className="crm-mail-meta">
                <span className="crm-line">
                  <span className="crm-compose-key">To</span>
                  <span>{String(sent.to ?? prepared.recipient)}</span>
                </span>
                <span className="crm-line">
                  <Glyph name="chat" />
                  <span>{thread ? thread.subject || "No subject" : "New thread"}</span>
                </span>
              </div>
              <div className="crm-card-foot">
                {taskDone ? (
                  <span className="crm-line is-ok">
                    <Icon name="check" />
                    Task marked done
                  </span>
                ) : (
                  <button
                    type="button"
                    className="crm-btn"
                    disabled={busy !== null}
                    onClick={() => void run("complete", completeTask)}
                  >
                    <Icon name="check" />
                    {busy === "complete" ? "Marking done…" : "Mark task done"}
                  </button>
                )}
              </div>
              <AfterSendReminder key={String(sent.message_id)} personId={personId} sent={sent} />
            </article>
          ) : (
            <div className="crm-card">
              <ul className="crm-compose-meta">
                <li>
                  <span className="crm-compose-key">From</span>
                  <span>{draft?.account ?? prepared.account ?? "Gmail not connected"}</span>
                </li>
                <li>
                  <span className="crm-compose-key">To</span>
                  <span>{draft?.to ?? prepared.recipient ?? "No email on file"}</span>
                </li>
                <li>
                  <Glyph name="chat" />
                  {prepared.threads.length > 1 && !draft ? (
                    <select
                      aria-label="Thread"
                      value={thread?.thread_id ?? ""}
                      disabled={busy !== null}
                      onChange={(event) => setThreadId(event.currentTarget.value || null)}
                    >
                      {prepared.threads.map((row) => (
                        <option key={row.thread_id} value={row.thread_id}>
                          {threadLine(row)}
                        </option>
                      ))}
                    </select>
                  ) : thread ? (
                    <>
                      <span>{threadLine(thread)}</span>
                      <a className="crm-btn is-ghost" href={thread.url} target="_blank" rel="noreferrer">
                        <Icon name="external" />
                        Open in Gmail
                      </a>
                    </>
                  ) : (
                    <span>New thread</span>
                  )}
                </li>
              </ul>

              {!draft ? (
                <form
                  className="crm-compose follow-up-editor"
                  aria-label="Follow-up message"
                  onSubmit={(event) => {
                    event.preventDefault();
                    void run("draft", async () => {
                      const session = await boundSession();
                      const created = await createFollowUpDraft(personId, {
                        sessionId: session,
                        body,
                        ...(replying ? { threadId: thread.thread_id } : { subject }),
                        taskId,
                      });
                      setDraft(created);
                      remember({ draftId: created.id });
                    });
                  }}
                >
                  <input
                    className="crm-compose-subject"
                    aria-label="Subject"
                    placeholder="Subject"
                    value={subject}
                    readOnly={replying}
                    title={replying ? "Replies keep the thread’s subject so Gmail keeps them together" : undefined}
                    required
                    onChange={(event) => {
                      setSubject(event.currentTarget.value);
                      remember({ subject: event.currentTarget.value });
                    }}
                  />
                  <textarea
                    className="crm-compose-body"
                    aria-label="Message"
                    placeholder="Write your message"
                    value={body}
                    rows={10}
                    required
                    onChange={(event) => {
                      setBody(event.currentTarget.value);
                      remember({ body: event.currentTarget.value });
                    }}
                  />
                  <div className="crm-card-foot">
                    <div className="crm-actions">
                      <button
                        type="submit"
                        className="crm-btn is-primary"
                        disabled={busy !== null || cannotDraft || !body.trim()}
                      >
                        <Icon name={replying ? "reply" : "draft"} />
                        {busy === "draft" ? "Creating draft…" : replying ? "Create reply draft in Gmail" : "Create Gmail draft"}
                      </button>
                      <button
                        type="button"
                        className="crm-btn"
                        disabled={busy !== null}
                        onClick={() => void run("assistant", askAssistant)}
                      >
                        <Icon name="assistant" />
                        {busy === "assistant" ? "Opening chat…" : "Ask assistant to write"}
                      </button>
                    </div>
                  </div>
                </form>
              ) : (
                <div className="crm-draft follow-up-review">
                  <div className="crm-mail-meta">
                    <span className="crm-pill is-warn">Not sent · Gmail draft</span>
                    <strong className="crm-mail-subject">{draft.subject}</strong>
                  </div>
                  {editing !== null ? (
                    <form
                      className="crm-compose follow-up-editor"
                      aria-label="Edit Gmail draft"
                      onKeyDown={(event) => {
                        if (event.key !== "Escape" || busy || editing !== draft.body) return;
                        event.preventDefault();
                        event.stopPropagation();
                        setEditing(null);
                      }}
                      onSubmit={(event) => {
                        event.preventDefault();
                        void run("save", async () => {
                          const session = await boundSession();
                          // A changed message needs a new approval, so deny the old one.
                          await denyPending();
                          const saved = await editOutreachDraft(personId, draft.id, {
                            sessionId: session,
                            body: editing,
                            expectedBodyDigest: draft.body_digest,
                            ...(replying ? {} : { subject: draft.subject }),
                          });
                          setDraft(saved);
                          setEditing(null);
                          setBody(saved.body);
                          remember({ body: saved.body, draftId: saved.id });
                        });
                      }}
                    >
                      <textarea
                        className="crm-compose-body"
                        aria-label="Message"
                        value={editing}
                        rows={10}
                        required
                        autoFocus
                        onChange={(event) => setEditing(event.currentTarget.value)}
                      />
                      <div className="crm-card-foot">
                        <div className="crm-actions">
                          <button type="submit" className="crm-btn is-primary" disabled={busy !== null || !editing.trim()}>
                            <Icon name="save" />
                            {busy === "save" ? "Saving…" : "Save to Gmail draft"}
                          </button>
                          <button type="button" className="crm-btn is-ghost" disabled={busy !== null} onClick={() => setEditing(null)}>
                            <Icon name="close" />
                            Discard edit
                          </button>
                        </div>
                      </div>
                    </form>
                  ) : (
                    <>
                      <pre className="crm-mail-body" aria-label="Exact message">
                        {draft.body}
                      </pre>
                      <div className="crm-card-foot">
                        <DraftVersion digest={draft.body_digest} />
                        <div className="crm-actions">
                          <button
                            type="button"
                            className="crm-btn"
                            disabled={busy !== null}
                            onClick={() => setEditing(draft.body)}
                          >
                            <Icon name="edit" />
                            Edit message
                          </button>
                          <button
                            type="button"
                            className="crm-btn"
                            disabled={busy !== null}
                            onClick={() =>
                              void run("reread", async () => {
                                setDraft(await readOutreachDraft(personId, draft.id));
                              })
                            }
                          >
                            <Icon name="refresh" />
                            {busy === "reread" ? "Re-reading…" : "Re-read from Gmail"}
                          </button>
                          {!approval ? (
                            <button
                              type="button"
                              className="crm-btn is-primary"
                              disabled={busy !== null || cannotDraft}
                              onClick={() =>
                                void run("approval", async () => {
                                  setApproval(
                                    await requestSendApproval(personId, {
                                      sessionId: await boundSession(),
                                      draftId: draft.id,
                                      reviewedBodyDigest: draft.body_digest,
                                      expectedThreadId: thread?.thread_id ?? null,
                                      taskId,
                                    }),
                                  );
                                })
                              }
                            >
                              <Icon name="approve" />
                              {busy === "approval" ? "Requesting…" : "Request approval to send"}
                            </button>
                          ) : null}
                        </div>
                      </div>
                    </>
                  )}

                  {approval && editing === null ? (
                    <SendApproval
                      approval={approval}
                      reviewed={reviewedHere}
                      busy={busy}
                      where={approval.resource.thread_id ? "in the thread above" : "as a new thread"}
                      onAllow={() =>
                        void run("allow", async () => {
                          const outcome = await decideApproval(approval.id, "allow");
                          setApproval(null);
                          if (outcome.ok) {
                            setSent(outcome.result);
                            setDraft(null);
                            clearFollowUpText(personId, taskId);
                          } else {
                            setProblem(String(outcome.result.error ?? "The send did not go out."));
                          }
                        })
                      }
                      onDeny={() => void run("deny", denyPending)}
                    />
                  ) : null}
                </div>
              )}
            </div>
          )}

          <details className="follow-up-points">
            <summary>
              <Glyph name="chevron" />
              <span>Talking points and sources</span>
              <span className="task-group-count">{prepared.talking_points.length}</span>
            </summary>
            <ul>
              {prepared.talking_points.map((point, index) => (
                <li key={`${point.origin}-${index}`}>{point.text}</li>
              ))}
            </ul>
            <p className="follow-up-sources">
              {prepared.sources.length > 0
                ? `Sources: ${[...new Set(prepared.sources.map((row) => row.provider))].join(", ")}`
                : "No sources filed yet."}
              {prepared.restricted_source_count > 0
                ? ` ${prepared.restricted_source_count} restricted source withheld.`
                : ""}
            </p>
          </details>
        </>
      )}

      {problem ? (
        <p className="crm-alert person-outreach-error" role="alert">
          <Glyph name="info" />
          {problem}
        </p>
      ) : null}
      <UndoToast toast={toast} onDismiss={dismiss} />
    </section>
  );
}
