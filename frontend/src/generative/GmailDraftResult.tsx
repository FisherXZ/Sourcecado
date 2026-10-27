import { useId, useState } from "react";

import type { DomainRendererProps } from "../chat/toolRegistry";
import {
  CardField,
  CardRow,
  CardRows,
  ExpandableText,
  Gap,
  InspectButton,
  rawToolTarget,
  ResultCard,
  SkeletonRows,
  UnsupportedCard,
} from "./ResultCard";

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

const NOT_SENT = "Not sent";

export function GmailDraftResult({
  args,
  result,
  status,
  toolCallId,
  toolName,
  failure,
  cardId,
}: DomainRendererProps) {
  if (toolName !== "gmail_draft" && toolName !== "gmail_create_draft") {
    return null;
  }
  const common = {
    id: cardId,
    toolCallIds: [toolCallId],
    provider: "gmail" as const,
    title: "Gmail draft",
  };
  if (status === "loading") {
    return (
      <ResultCard {...common} tag={NOT_SENT} state="loading">
        <SkeletonRows label="Creating Gmail draft preview" />
      </ResultCard>
    );
  }
  if (status === "error") {
    return (
      <ResultCard
        {...common}
        state={failure?.class === "permission" ? "restricted" : "failed"}
        note={`${NOT_SENT} · Gmail draft was not created.`}
      />
    );
  }
  if (status !== "success") return null;
  const raw = record(result);
  const input = record(args);
  const draftId = text(raw?.id) ?? text(raw?.draft_id);
  if (!raw || !draftId || raw.sent === true || raw.drafted === false) {
    return (
      <UnsupportedCard
        cardId={cardId}
        toolCallIds={[toolCallId]}
        provider="gmail"
        title="Gmail draft"
        // A record that says it was sent must not be labelled "Not sent".
        tag={raw?.sent === true ? undefined : NOT_SENT}
        inspect={rawToolTarget({ toolCallId, toolName, args, result }, "Gmail draft")}
      />
    );
  }
  const recipient = text(raw?.to) ?? text(input?.to);
  const subject = text(raw?.subject) ?? text(input?.subject);
  const body = text(input?.body);
  const account = text(raw?.accountEmail) ?? text(raw?.account_email);
  const externalUrl =
    text(raw?.external_url) ?? text(raw?.externalUrl) ?? text(raw?.draftUrl);

  return (
    <ResultCard
      {...common}
      count="1 draft"
      tag={NOT_SENT}
      footer={
        <InspectButton
          target={{
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
          }}
        />
      }
    >
      <CardRows label="Gmail draft">
        <CardField icon="mail" label="To">
          {recipient ?? <Gap>No recipient</Gap>}
        </CardField>
        <CardField icon="doc" label="Subject">
          {subject ?? <Gap>No subject</Gap>}
        </CardField>
        <CardRow tall>
          {body ? (
            <ExpandableText
              text={body}
              limit={BODY_PREVIEW_LIMIT}
              expandLabel="Expand draft body"
              collapseLabel="Collapse draft body"
              className="sourcecado-card-body-text"
            />
          ) : (
            <Gap>No body</Gap>
          )}
        </CardRow>
        <CardField icon="link" label="Draft ID">
          {draftId}
        </CardField>
        <CardField icon="user" label="Account">
          {account ?? <Gap>Account address unavailable</Gap>}
        </CardField>
      </CardRows>
    </ResultCard>
  );
}
