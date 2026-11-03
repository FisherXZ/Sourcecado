import { useEffect, useRef, useState } from "react";
import type { PreparedText } from "./crm/SavedTaskDrafts";
import { AfterSendReminder } from "./AfterSendReminder";
import { Glyph } from "./generative/ResultCard";
import { Icon } from "./icons";

import {
  createOutreachDraft,
  decideApproval,
  readOutreachDraft,
  requestSendApproval,
  type OutreachDraft,
  type PendingSendApproval,
} from "./api";

/**
 * Draft, review, approve, send — for one bound person.
 *
 * The draft lives in Gmail, so that is where it gets edited. This panel shows
 * the version Gmail holds right now and binds the approval to the version the
 * director actually read. Editing in Gmail and re-reading here is the edit
 * loop; approving a version and then editing it sends nothing.
 */
export function OutreachPanel({
  personId,
  sessionId,
  recipient,
  prepared,
}: {
  readonly personId: string;
  readonly sessionId: string | null;
  readonly recipient: string | null;
  readonly prepared?: PreparedText;
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
  const subjectField = useRef<HTMLInputElement>(null);
  const draftButton = useRef<HTMLButtonElement>(null);
  const focusNext = useRef<"subject" | "start" | null>(null);
  const blank = subject.trim() === "" && body.trim() === "";
  // Typed text keeps the form open, so it is never hidden unsaved.
  const formOpen = composing || !blank;

  useEffect(() => {
    const target = focusNext.current === "subject" ? subjectField.current
      : focusNext.current === "start" ? draftButton.current : null;
    focusNext.current = null;
    target?.focus();
  }, [formOpen]);

  function openCompose() {
    focusNext.current = "subject";
    setComposing(true);
  }

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

  if (!sessionId) {
    return (
      <section className="person-section" aria-labelledby="person-outreach-heading">
        <h2 id="person-outreach-heading">Outreach</h2>
        <p className="person-empty">Open this person’s sourcing chat first.</p>
      </section>
    );
  }

  if (!recipient) {
    return (
      <section className="person-section" aria-labelledby="person-outreach-heading">
        <h2 id="person-outreach-heading">Outreach</h2>
        <p className="person-empty">This person has no email address yet.</p>
      </section>
    );
  }

  return (
    <section className="person-section" aria-labelledby="person-outreach-heading">
      <h2 id="person-outreach-heading">Outreach</h2>

      {sent ? (
        <SentCard
          sent={sent}
          at={sentAt}
          recipient={recipient}
          personId={personId}
        />
      ) : null}

      {!draft && !sent ? (
        <>
        {prepared && !prepared.thread_id && !prepared.thread_review_required ? <button type="button" className="person-outreach-prepared" onClick={() => { setSubject(prepared.subject); setBody(prepared.body); openCompose(); }}><Icon name="draft" />Use prepared follow-up text</button> : null}
        {formOpen ? (
        <form
          className="person-outreach-compose"
          onSubmit={(event) => {
            event.preventDefault();
            void run("draft", async () => {
              setDraft(
                await createOutreachDraft(personId, {
                  sessionId,
                  subject,
                  body,
                }),
              );
            });
          }}
        >
          <p className="person-outreach-recipient">
            <Glyph name="mail" />
            <span>To</span> <strong>{recipient}</strong>
          </p>
          <label htmlFor="outreach-subject">Subject</label>
          <input
            ref={subjectField}
            id="outreach-subject"
            value={subject}
            onChange={(event) => setSubject(event.target.value)}
            required
          />
          <label htmlFor="outreach-body">Message</label>
          <textarea
            id="outreach-body"
            value={body}
            rows={8}
            onChange={(event) => setBody(event.target.value)}
            required
          />
          <div className="person-outreach-actions">
            <button type="submit" disabled={busy !== null}>
              <Icon name="draft" />
              {busy === "draft" ? "Creating draft…" : "Create Gmail draft"}
            </button>
            <button
              type="button"
              disabled={busy !== null || !blank}
              title={blank ? undefined : "Clear the subject and message to close"}
              onClick={() => {
                focusNext.current = "start";
                setComposing(false);
              }}
            >
              <Icon name="close" />
              Cancel
            </button>
          </div>
        </form>
        ) : (
          <div className="person-outreach-start">
            <button ref={draftButton} type="button" onClick={openCompose}>
              <Icon name="draft" />
              Draft email
            </button>
            <p className="person-outreach-recipient">
              <Glyph name="mail" />
              <span>To</span> <strong>{recipient}</strong>
            </p>
          </div>
        )}
        </>
      ) : null}

      {draft && !sent ? (
        <div className="person-outreach-review">
          <p className="person-outreach-account">
            Gmail · {draft.account ?? "Account could not be determined"}
          </p>
          <strong className="person-outreach-status">Not sent</strong>
          <dl className="person-outreach-fields">
            <div>
              <dt><Glyph name="mail" />To</dt>
              <dd>{draft.to}</dd>
            </div>
            <div>
              <dt><Glyph name="doc" />Subject</dt>
              <dd>{draft.subject}</dd>
            </div>
          </dl>
          <pre className="person-outreach-body">{draft.body}</pre>
          <p className="person-outreach-version">
            Reviewed version <code>{draft.body_digest.slice(0, 12)}</code>
          </p>
          <div className="person-outreach-actions">
            <button
              type="button"
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
                disabled={busy !== null}
                onClick={() =>
                  void run("approval", async () => {
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

          {approval ? (
            <div className="person-outreach-approval">
              <p>
                Send <strong>{approval.resource.subject}</strong> to{" "}
                <strong>{approval.resource.to}</strong> from{" "}
                {approval.resource.account ?? "the connected account"}, body
                version <code>{approval.resource.body_digest.slice(0, 12)}</code>.
              </p>
              {reviewedHere ? null : (
                <p role="alert">
                  This draft changed after the approval was requested. Re-read it
                  and ask again — allowing now sends nothing.
                </p>
              )}
              <div className="person-outreach-actions">
                <button
                  type="button"
                  disabled={busy !== null}
                  onClick={() =>
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
                        setProblem(
                          String(outcome.result.error ?? "The send did not go out."),
                        );
                      }
                    })
                  }
                >
                  <Icon name="send" />
                  {busy === "allow" ? "Sending…" : "Allow once and send"}
                </button>
                <button
                  type="button"
                  disabled={busy !== null}
                  onClick={() =>
                    void run("deny", async () => {
                      await decideApproval(approval.id, "deny");
                      setApproval(null);
                    })
                  }
                >
                  <Icon name="deny" />
                  {busy === "deny" ? "Denying…" : "Deny"}
                </button>
              </div>
            </div>
          ) : null}
        </div>
      ) : null}

      {problem ? (
        <p className="person-outreach-error" role="alert">
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
    <div className="person-outreach-sent" role="status">
      <p className="person-outreach-sent-head">
        <Icon name="send" />
        <strong>Sent</strong>
        {at ? (
          <time dateTime={at.toISOString()}>
            {" · "}
            {at.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}
          </time>
        ) : null}
        {target ? (
          <a
            className="person-outreach-gmail"
            href={gmailLink(target)}
            target="_blank"
            rel="noreferrer"
            title={`Gmail message ${message ?? "unknown"} · thread ${thread ?? "unknown"}`}
          >
            <Icon name="external" />
            Open in Gmail
          </a>
        ) : null}
      </p>
      <dl className="person-outreach-fields">
        <div>
          <dt><Glyph name="mail" />To</dt>
          <dd>{text(sent.to) ?? recipient}</dd>
        </div>
        {subject ? (
          <div>
            <dt><Glyph name="doc" />Subject</dt>
            <dd>{subject}</dd>
          </div>
        ) : null}
      </dl>
      <AfterSendReminder key={String(sent.message_id)} personId={personId} sent={sent} />
    </div>
  );
}
