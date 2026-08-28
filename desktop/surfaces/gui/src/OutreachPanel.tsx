import { useState } from "react";

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
}: {
  readonly personId: string;
  readonly sessionId: string | null;
  readonly recipient: string | null;
}) {
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [draft, setDraft] = useState<OutreachDraft | null>(null);
  const [approval, setApproval] = useState<PendingSendApproval | null>(null);
  const [sent, setSent] = useState<Record<string, unknown> | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

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
        <p className="person-empty">
          Open this person’s sourcing chat first. Outreach starts from the chat
          that is bound to them, so the recipient is never guessed.
        </p>
      </section>
    );
  }

  if (!recipient) {
    return (
      <section className="person-section" aria-labelledby="person-outreach-heading">
        <h2 id="person-outreach-heading">Outreach</h2>
        <p className="person-empty">
          This person file has no email address yet. Enrich or add one before
          drafting.
        </p>
      </section>
    );
  }

  return (
    <section className="person-section" aria-labelledby="person-outreach-heading">
      <h2 id="person-outreach-heading">Outreach</h2>

      {sent ? (
        <div className="person-outreach-sent" role="status">
          <strong>Sent</strong>
          <dl>
            <div>
              <dt>To</dt>
              <dd>{recipient}</dd>
            </div>
            <div>
              <dt>Gmail message</dt>
              <dd>
                <code>{String(sent.message_id ?? "unknown")}</code>
              </dd>
            </div>
            <div>
              <dt>Gmail thread</dt>
              <dd>
                <code>{String(sent.thread_id ?? "unknown")}</code>
              </dd>
            </div>
          </dl>
        </div>
      ) : null}

      {!draft && !sent ? (
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
            To <strong>{recipient}</strong> — taken from this person file.
          </p>
          <label htmlFor="outreach-subject">Subject</label>
          <input
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
          <button type="submit" disabled={busy !== null}>
            {busy === "draft" ? "Creating draft…" : "Create Gmail draft"}
          </button>
        </form>
      ) : null}

      {draft && !sent ? (
        <div className="person-outreach-review">
          <p className="person-outreach-account">
            Gmail · {draft.account ?? "Account could not be determined"}
          </p>
          <strong className="person-outreach-status">Not sent</strong>
          <dl>
            <div>
              <dt>To</dt>
              <dd>{draft.to}</dd>
            </div>
            <div>
              <dt>Subject</dt>
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
                        setSent(outcome.result);
                        setDraft(null);
                      } else {
                        setProblem(
                          String(outcome.result.error ?? "The send did not go out."),
                        );
                      }
                    })
                  }
                >
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
