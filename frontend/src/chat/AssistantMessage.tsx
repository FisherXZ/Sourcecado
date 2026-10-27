import {
  MessagePrimitive,
  useAuiState,
  type TextMessagePartProps,
  type SourceMessagePart,
  type ToolCallMessagePartProps,
  type ToolCallMessagePart,
} from "@assistant-ui/react";
import { useState } from "react";
import { ActivityGroup } from "./ActivityGroup";
import { AnswerMarkdown } from "./AnswerMarkdown";
import { ApprovalCard } from "./ApprovalCard";
import { AssistantActions } from "./MessageActions";
import { RunBudgetView } from "./RunBudget";
import { CompactIcon } from "./UsageLine";
import { useLastApprovalDelivery } from "./approvalDelivery";
import { AnswerResults } from "./AnswerResults";
import { useInspector } from "./Inspector";
import { answerHasStarted, isLiveMessage } from "./processState";
import { ThinkingRow } from "./ThinkingRow";
import { TurnNoticeHead } from "./TurnNotice";
import {
  joinReasoning,
  reasoningParts,
  reasoningTiming,
  thinkingElapsedMs,
  thoughtLabel,
} from "./thinking";
import { resolveInbox } from "../api";
import type { RunBudgetStatus } from "./protocol";

function AssistantText({ text }: TextMessagePartProps) {
  return <AnswerMarkdown text={text} />;
}

function noticeFrame(code: unknown): {
  readonly title: string;
  readonly detail: string | null;
} {
  if (code === "transport") {
    return {
      title: "Connection problem.",
      detail: "Sourcecado will keep trying to reconnect.",
    };
  }
  if (code === "compacted") {
    return { title: "Context compacted", detail: null };
  }
  if (code === "unsupported_version" || code === "malformed_event") {
    return {
      title: "Some conversation history is unavailable.",
      detail: "Available messages are shown.",
    };
  }
  if (
    code === "provider_runtime_error" ||
    code === "invalid_request" ||
    code === "protocol" ||
    code === "configuration"
  ) {
    return {
      title: "Model provider compatibility problem.",
      detail: "Sourcecado tried compatible configured providers. Retry once, then review Settings if it persists.",
    };
  }
  return { title: "This turn failed.", detail: null };
}

function SafeToolFallback(part: ToolCallMessagePartProps) {
  const lastDelivery = useLastApprovalDelivery();
  if (!part.approval) return null;
  return (
    <ApprovalCard
      part={part}
      onDecision={async (approved, scope = "once") => {
        if (approved && scope === "always") {
          await resolveInbox(part.approval!.id, "allow", "always");
          window.dispatchEvent(new Event("sourcecado:inbox-changed"));
          return;
        }
        // assistant-ui's respondToApproval bridge discards the adapter's
        // return value, so the real delivery outcome is read back out of
        // lastDelivery (see approvalDelivery.tsx) right after this
        // synchronous call completes.
        part.respondToApproval({ approved });
        const delivery = lastDelivery.current;
        if (delivery?.state === "dropped") {
          throw new Error(delivery.reason);
        }
        if (delivery?.state === "queued") {
          return "queued";
        }
      }}
    />
  );
}

export function AssistantMessage() {
  const message = useAuiState((state) => state.message);
  // The operator's own open or close choice for the thinking row. It lives
  // here so it survives the row moving between the one-row and receipt layouts.
  const [thinkingChoice, setThinkingChoice] = useState<boolean | null>(null);
  const sourcecadoState = message.metadata.custom?.sourcecadoState;
  const runBudget = message.metadata.custom?.sourcecadoRunBudget as
    | RunBudgetStatus
    | undefined;
  const notice = message.metadata.custom?.sourcecadoNotice === true;
  const compacted =
    notice && message.metadata.custom?.sourcecadoNoticeCode === "compacted";
  const frame = notice
    ? noticeFrame(message.metadata.custom?.sourcecadoNoticeCode)
    : null;
  // A notice about a turn that failed, as opposed to a connection or history notice.
  const failedTurn =
    notice && message.metadata.custom?.sourcecadoNoticeFailedTurn === true;
  const prose = message.content
    .filter((part) => part.type === "text")
    .map((part) => (part.type === "text" ? part.text : ""))
    .join("\n");
  const copyable =
    message.status?.type === "complete" && prose.trim().length > 0;
  const tools = message.content.filter(
    (part): part is ToolCallMessagePart => part.type === "tool-call",
  );
  const sources = message.content.filter(
    (part): part is SourceMessagePart => part.type === "source",
  );
  const answerStarted = answerHasStarted(message.content);
  const reasoning = reasoningParts(message.content);
  const thinking =
    isLiveMessage(sourcecadoState) &&
    reasoning.some((part) => part.status?.type === "running");
  const timing = reasoningTiming(reasoning);
  const lead =
    reasoning.length > 0
      ? {
          summary: thinking
            ? "Thinking…"
            : thoughtLabel(thinkingElapsedMs(timing, Date.now())),
          node: (
            <ThinkingRow
              text={joinReasoning(reasoning)}
              thinking={thinking}
              timing={timing}
              chosen={thinkingChoice}
              onChoose={setThinkingChoice}
            />
          ),
        }
      : undefined;
  const artifacts = Array.isArray(
    message.metadata.custom?.sourcecadoArtifacts,
  )
    ? (message.metadata.custom?.sourcecadoArtifacts as Array<
        Record<string, unknown>
      >)
    : [];

  return (
    <MessagePrimitive.Root
      className={`sourcecado-assistant-message${notice ? " sourcecado-notice" : ""}${compacted ? " sourcecado-compaction-receipt" : ""}${failedTurn ? " sourcecado-notice-failed" : ""}`}
      {...(notice ? { role: "note" } : {})}
    >
      {frame ? (
        failedTurn ? (
          <TurnNoticeHead
            title={frame.title}
            provider={message.metadata.custom?.sourcecadoNoticeProvider}
            model={message.metadata.custom?.sourcecadoNoticeModel}
          />
        ) : (
          <p className="sourcecado-notice-title">
            {compacted ? <CompactIcon /> : null}
            {frame.title}
          </p>
        )
      ) : null}
      <ActivityGroup
        tools={tools}
        messageState={sourcecadoState}
        answerStarted={answerStarted}
        {...(lead ? { lead } : {})}
      />
      <MessagePrimitive.Parts
        components={{
          Text: AssistantText,
          tools: { Fallback: SafeToolFallback },
        }}
      />
      <AnswerResults tools={tools} sources={sources} messageState={sourcecadoState} />
      <RunBudgetView status={runBudget} messageState={sourcecadoState} />
      {artifacts.length > 0 ? <ArtifactControls artifacts={artifacts} /> : null}
      {sourcecadoState === "cancelled" ? (
        <p className="sourcecado-terminal-receipt">Run cancelled.</p>
      ) : null}
      {frame?.detail ? (
        <p className="sourcecado-notice-detail">{frame.detail}</p>
      ) : null}
      {copyable ? (
        <AssistantActions
          prose={prose}
          tools={tools}
          messageState={sourcecadoState}
        />
      ) : null}
    </MessagePrimitive.Root>
  );
}

function ArtifactControls({
  artifacts,
}: {
  readonly artifacts: readonly Record<string, unknown>[];
}) {
  const { select } = useInspector();
  return (
    <div className="sourcecado-artifacts" aria-label="Artifacts">
      {artifacts.map((artifact) => {
        const id = String(artifact.id ?? "artifact");
        const title = String(artifact.title ?? "Generated artifact");
        return (
          <button
            key={id}
            type="button"
            aria-label={`Artifact: ${title}`}
            onClick={(event) =>
              select(
                {
                  kind: "artifact",
                  id,
                  title,
                  status: "success",
                  preview:
                    typeof artifact.preview === "string"
                      ? artifact.preview
                      : null,
                  externalUrl:
                    typeof artifact.external_url === "string"
                      ? artifact.external_url
                      : null,
                  stale: artifact.stale === true,
                  truncated: artifact.truncated === true,
                },
                event.currentTarget,
              )
            }
          >
            {title}
          </button>
        );
      })}
    </div>
  );
}
