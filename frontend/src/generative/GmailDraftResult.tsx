import { useId, useState } from "react";

import { useInspector } from "../chat/Inspector";
import type { DomainRendererProps } from "../chat/toolRegistry";

const BODY_PREVIEW_LIMIT = 280;

export function GmailDraftBody({ body }: { readonly body: string }) {
  const [expanded, setExpanded] = useState(false);
  const bodyId = useId();
  const truncated = body.length > BODY_PREVIEW_LIMIT;
  const visibleBody =
    truncated && !expanded
      ? `${body.slice(0, BODY_PREVIEW_LIMIT).trimEnd()}…`
      : body;

  return (
    <div className="sourcecado-gmail-body-preview">
      <p id={bodyId} className="sourcecado-gmail-body">
        {visibleBody}
      </p>
      {truncated ? (
        <button
          type="button"
          aria-expanded={expanded}
          aria-controls={bodyId}
          onClick={() => setExpanded((current) => !current)}
        >
          {expanded ? "Collapse draft body" : "Expand draft body"}
        </button>
      ) : null}
    </div>
  );
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

export function GmailDraftResult({
  args,
  result,
  status,
  toolCallId,
  toolName,
}: DomainRendererProps) {
  const { select } = useInspector();
  if (toolName !== "gmail_draft" && toolName !== "gmail_create_draft") {
    return null;
  }
  if (status === "loading") {
    return (
      <section className="sourcecado-gmail-draft sourcecado-gmail-creating">
        <header>
          <h3>Creating Gmail draft</h3>
          <strong>Not sent</strong>
        </header>
        <ol aria-label="Creating Gmail draft preview" aria-busy="true">
          {[0, 1, 2].map((row) => (
            <li key={row}>
              <span />
            </li>
          ))}
        </ol>
      </section>
    );
  }
  if (status === "error") {
    return (
      <p className="sourcecado-gmail-failed">
        Not sent · Gmail draft was not created.
      </p>
    );
  }
  if (status !== "success") return null;
  const raw = record(result);
  const input = record(args);
  const draftId = text(raw?.id) ?? text(raw?.draft_id);
  if (!raw || !draftId || raw.sent === true || raw.drafted === false) {
    return (
      <section className="sourcecado-gmail-draft sourcecado-gmail-fallback">
        <h3>Gmail draft result needs review</h3>
        <strong>Not sent</strong>
        <p>Sourcecado couldn’t verify a reviewable Gmail draft. Use Inspect above to review the result.</p>
      </section>
    );
  }
  const recipient = text(raw?.to) ?? text(input?.to);
  const subject = text(raw?.subject) ?? text(input?.subject);
  const body = text(input?.body);
  const account = text(raw?.accountEmail) ?? text(raw?.account_email);
  const externalUrl =
    text(raw?.external_url) ?? text(raw?.externalUrl) ?? text(raw?.draftUrl);

  return (
    <section className="sourcecado-gmail-draft" aria-label="Gmail draft artifact">
      <header>
        <div>
          <h3>Gmail draft ready for review</h3>
          <strong>Not sent</strong>
        </div>
        {draftId ? <p>Draft ID: {draftId}</p> : null}
      </header>
      {recipient ? <p>{recipient}</p> : null}
      {subject ? <p>{subject}</p> : null}
      {body ? <GmailDraftBody body={body} /> : null}
      {account ? (
        <p>Gmail · {account}</p>
      ) : (
        <p>Google account address unavailable; draft is still available.</p>
      )}
      <div className="sourcecado-gmail-actions">
        <button
          type="button"
          aria-label={`Inspect Gmail draft ${draftId}`}
          onClick={(event) =>
            select(
              {
                kind: "artifact",
                id: `gmail-draft:${toolCallId}:${draftId}`,
                title: `Gmail draft: ${subject ?? draftId}`,
                status: "success",
                provider: "Gmail",
                externalUrl,
                preview: body ? body.slice(0, BODY_PREVIEW_LIMIT) : null,
                result: {
                  draftId,
                  recipient,
                  subject,
                  account,
                  status: "not_sent",
                },
              },
              event.currentTarget,
            )
          }
        >
          Draft details
        </button>
        <button
          type="button"
          aria-label="Inspect Gmail source"
          onClick={(event) =>
            select(
              {
                kind: "source",
                id: `gmail-source:${toolCallId}`,
                title: "Gmail source",
                status: "success",
                provider: "Gmail",
                preview: account ?? "Connected Google account address unavailable",
                result: { resource: "Gmail drafts", account },
              },
              event.currentTarget,
            )
          }
        >
          Source
        </button>
      </div>
    </section>
  );
}
