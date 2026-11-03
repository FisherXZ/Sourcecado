import { useEffect, useRef, useState } from "react";
import type { PreparedText } from "./crm/SavedTaskDrafts";
import { DraftVersion, SendApproval } from "./crm/SendApproval";
import { AfterSendReminder } from "./AfterSendReminder";
import { Glyph } from "./generative/ResultCard";
import { Icon } from "./icons";

import {
  createOutreachDraft,
  decideApproval,
  openPersonSourcingChat,
  readOutreachDraft,
  requestSendApproval,
  type OutreachDraft,
  type PendingSendApproval,
} from "./api";

/**
 * Draft, review, approve, send — for one person.
 *
 * Drafting needs the person's sourcing chat. Without one, Draft email opens
 * it first (the same binding Start chat makes) and tells the person file to
 * reload, so its header shows Open chat.
 *
 * The draft lives in Gmail, so that is where it gets edited. This panel shows
 * the version Gmail holds right now and binds the approval to the version the
 * director actually read. Editing in Gmail and re-reading here is the edit
 * loop; approving a version and then editing it sends nothing.
 */
export function OutreachPanel({
  personId,
  sessionId: boundSessionId,
  personVersion = null,
  recipient,
  prepared,
  onChatOpened,
}: {
  readonly personId: string;
  readonly sessionId: string | null;
  /** Needed to open the sourcing chat when there is none yet. */
  readonly personVersion?: number | null;
  readonly recipient: string | null;
  readonly prepared?: PreparedText;
  /** Called after Draft email opened a new sourcing chat. */
  readonly onChatOpened?: () => void;
}) {
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [draft, setDraft] = useState<OutreachDraft | null>(null);
  const [approval, setApproval] = useState<PendingSendApproval | null>(null);
  const [sent, setSent] = useState<Record<string, unknown> | null>(null);
  const [sentAt, setSentAt] = useState<Date | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [composing, setComposing] = useState(false);
  const [openedSessionId, setOpenedSessionId] = useState<string | null>(null);
  const subjectField = useRef<HTMLInputElement>(null);
  const draftButton = useRef<HTMLButtonElement>(null);
  const focusNext = useRef<"subject" | "start" | null>(null);
  const sessionId = boundSessionId ?? openedSessionId;
  const blank = subject.trim() === "" && body.trim() === "";
  // Typed text keeps the form open, so it is never hidden unsaved.
  const formOpen = composing || !blank;
  const usablePrepared =
    prepared && !prepared.thread_id && !prepared.thread_review_required ? prepared : null;

  useEffect(() => {
    const target = focusNext.current === "subject" ? subjectField.current
      : focusNext.current === "start" ? draftButton.current : null;
    focusNext.current = null;
    target?.focus();
  }, [formOpen]);

  const reviewedHere =
    approval !== null &&
    draft !== null &&
    approval.resource.body_digest === draft.body_digest;

  async function run(label: string, work: () => Promise<void>) {
    if (busy) return;
    setBusy(label);
    setProblem(null);
    try {
      await work();
    } catch (error) {
      setProblem(error instanceof Error ? error.message : "Something went wrong.");
    } finally {
      setBusy(null);
    }
  }

  function openCompose(text?: PreparedText) {
    if (text) {
      setSubject(text.subject);
      setBody(text.body);
    }
    if (formOpen) {
      subjectField.current?.focus();
      return;
    }
    focusNext.current = "subject";
    setComposing(true);
  }

  /** Opens the compose form, binding the sourcing chat first when there is none. */
  function startDraft(text?: PreparedText) {
    if (sessionId) {
      openCompose(text);
      return;
    }
    void run("chat", async () => {
      if (personVersion === null) throw new Error("Reload this person file, then try again.");
      let opened;
      try {
        opened = await openPersonSourcingChat(personId, personVersion);
      } catch {
        throw new Error("Couldn’t open this person’s sourcing chat. Try again.");
      }
      if (opened.active_person.person_id !== personId) {
        throw new Error("That chat belongs to a different person. Nothing was drafted.");
      }
      setOpenedSessionId(opened.session.id);
      onChatOpened?.();
      openCompose(text);
    });
  }

  if (!recipient) {
    return (
      <section className="person-section person-outreach" aria-labelledby="person-outreach-heading">
        <h2 id="person-outreach-heading">Outreach</h2>
        <p className="crm-line">
          <Glyph name="mail" />
          No email address yet
        </p>
      </section>
    );
  }

  return (
    <section className="person-section person-outreach" aria-labelledby="person-outreach-heading">
      <h2 id="person-outreach-heading">Outreach</h2>

      {sent ? (
        <SentCard sent={sent} at={sentAt} recipient={recipient} personId={personId} />
      ) : null}

      {!draft && !sent ? (
        formOpen ? (
          <form
            className="crm-card crm-compose"
            aria-label="Compose email"
            onKeyDown={(event) => {
              if (event.key !== "Escape" || !blank || busy) return;
              event.preventDefault();
              event.stopPropagation();
              focusNext.current = "start";
              setComposing(false);
            }}
            onSubmit={(event) => {
              event.preventDefault();
              void run("draft", async () => {
                if (!sessionId) throw new Error("Open this person’s sourcing chat first.");
                setDraft(await createOutreachDraft(personId, { sessionId, subject, body }));
              });
            }}
          >
            <p className="crm-compose-row">
              <span className="crm-compose-key">To</span>
              <strong>{recipient}</strong>
            </p>
            <input
              ref={subjectField}
              className="crm-compose-subject"
              aria-label="Subject"
              placeholder="Subject"
              value={subject}
              onChange={(event) => setSubject(event.target.value)}
              required
            />
            <textarea
              className="crm-compose-body"
              aria-label="Message"
              placeholder="Write your message"
              value={body}
              rows={8}
              onChange={(event) => setBody(event.target.value)}
              required
            />
            <div className="crm-card-foot">
              <div className="crm-actions">
                <button type="submit" className="crm-btn is-primary" disabled={busy !== null}>
                  <Icon name="draft" />
                  {busy === "draft" ? "Creating draft…" : "Create Gmail draft"}
                </button>
                {usablePrepared && blank ? (
                  <button type="button" className="crm-btn" onClick={() => startDraft(usablePrepared)}>
                    <Icon name="draft" />
                    Use prepared follow-up text
                  </button>
                ) : null}
                <button
                  type="button"
                  className="crm-btn is-ghost"
                  disabled={busy !== null || !blank}
                  title={blank ? "Close (Escape)" : "Clear the subject and message to close"}
                  onClick={() => {
                    focusNext.current = "start";
                    setComposing(false);
                  }}
                >
                  <Icon name="close" />
                  Cancel
                </button>
              </div>
            </div>
          </form>
        ) : (
          <div className="person-outreach-start">
            <button
              ref={draftButton}
              type="button"
              className="crm-btn is-primary"
              disabled={busy !== null}
              onClick={() => startDraft()}
            >
              <Icon name="draft" />
              {busy === "chat" ? "Opening chat…" : "Draft email"}
            </button>
            {usablePrepared ? (
              <button
                type="button"
                className="crm-btn"
                disabled={busy !== null}
                onClick={() => startDraft(usablePrepared)}
              >
                <Icon name="draft" />
                Use prepared follow-up text
              </button>
            ) : null}
            <span className="crm-line">
              <Glyph name="mail" />
              <strong>{recipient}</strong>
            </span>
          </div>
        )
      ) : null}

      {draft && !sent ? (
        <article className="crm-card crm-draft" aria-label="Gmail draft">
          <header className="crm-card-head">
            <span className="crm-line">
              <Glyph name="mail" />
              <span>{draft.account ?? "Gmail account unknown"}</span>
            </span>
            <span className="crm-pill is-warn">Not sent</span>
          </header>
          <div className="crm-mail-meta">
            <strong className="crm-mail-subject">{draft.subject}</strong>
            <span className="crm-line">
              <span className="crm-compose-key">To</span>
              <span>{draft.to}</span>
            </span>
          </div>
          <pre className="crm-mail-body">{draft.body}</pre>
          <footer className="crm-card-foot">
            <DraftVersion digest={draft.body_digest} />
            <div className="crm-actions">
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
                {busy === "reread" ? "Re-reading…" : "Re-read draft from Gmail"}
              </button>
              {!approval ? (
                <button
                  type="button"
                  className="crm-btn is-primary"
                  disabled={busy !== null || !sessionId}
                  onClick={() =>
                    void run("approval", async () => {
                      if (!sessionId) return;
                      setApproval(
                        await requestSendApproval(personId, {
                          sessionId,
                          draftId: draft.id,
                          reviewedBodyDigest: draft.body_digest,
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
          </footer>

          {approval ? (
            <SendApproval
              approval={approval}
              reviewed={reviewedHere}
              busy={busy}
              onAllow={() =>
                void run("allow", async () => {
                  const outcome = await decideApproval(approval.id, "allow");
                  setApproval(null);
                  if (outcome.ok) {
                    // The reviewed subject and recipient fill in when the
                    // send result leaves them out.
                    setSent({
                      subject: approval.resource.subject,
                      to: approval.resource.to,
                      ...outcome.result,
                    });
                    setSentAt(new Date());
                    setDraft(null);
                  } else {
                    setProblem(String(outcome.result.error ?? "The send did not go out."));
                  }
                })
              }
              onDeny={() =>
                void run("deny", async () => {
                  await decideApproval(approval.id, "deny");
                  setApproval(null);
                })
              }
            />
          ) : null}
        </article>
      ) : null}

      {problem ? (
        <p className="crm-alert person-outreach-error" role="alert">
          <Glyph name="info" />
          {problem}
        </p>
      ) : null}
    </section>
  );
}

function gmailLink(id: string): string {
  return `https://mail.google.com/mail/u/0/#all/${encodeURIComponent(id)}`;
}

/** What went out, to whom, and where to open it. Gmail ids stay off screen. */
function SentCard({
  sent,
  at,
  recipient,
  personId,
}: {
  sent: Record<string, unknown>;
  at: Date | null;
  recipient: string;
  personId: string;
}) {
  const text = (value: unknown) =>
    typeof value === "string" && value.trim() ? value : null;
  const message = text(sent.message_id);
  const thread = text(sent.thread_id);
  const subject = text(sent.subject);
  const target = thread ?? message;
  return (
    <article className="crm-card crm-sent person-outreach-sent" role="status" aria-label="Sent email">
      <header className="crm-card-head">
        <span className="crm-line is-ok">
          <Icon name="send" />
          <strong>Sent</strong>
          {at ? (
            <time dateTime={at.toISOString()}>
              {at.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}
            </time>
          ) : null}
        </span>
        {target ? (
          <a
            className="crm-btn is-ghost"
            href={gmailLink(target)}
            target="_blank"
            rel="noreferrer"
            title={`Gmail message ${message ?? "unknown"} · thread ${thread ?? "unknown"}`}
          >
            <Icon name="external" />
            Open in Gmail
          </a>
        ) : null}
      </header>
      <div className="crm-mail-meta">
        {subject ? <strong className="crm-mail-subject">{subject}</strong> : null}
        <span className="crm-line">
          <span className="crm-compose-key">To</span>
          <span>{text(sent.to) ?? recipient}</span>
        </span>
      </div>
      <AfterSendReminder key={String(sent.message_id)} personId={personId} sent={sent} />
    </article>
  );
}
