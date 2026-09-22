import {
  MessagePrimitive,
  useAuiState,
  type TextMessagePartProps,
  type ToolCallMessagePartProps,
  type ToolCallMessagePart,
} from "@assistant-ui/react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ActivityGroup } from "./ActivityGroup";
import { ApprovalCard } from "./ApprovalCard";
import { RunBudgetView } from "./RunBudget";
import { useLastApprovalDelivery } from "./approvalDelivery";
import { SourceCitation } from "./SourceCitation";
import { useInspector } from "./Inspector";
import { resolveInbox } from "../api";
import type { RunBudgetStatus } from "./protocol";

function AssistantText({ text }: TextMessagePartProps) {
  return (
    <div className="sourcecado-markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ children, ...props }) => (
            <a {...props} target="_blank" rel="noreferrer noopener">
              {children}
            </a>
          ),
          table: ({ children, ...props }) => (
            <div className="sourcecado-table-scroll">
              <table {...props}>{children}</table>
            </div>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
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
    return {
      title: "Older context was compacted.",
      detail: "Available messages are shown.",
    };
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
  const sourcecadoState = message.metadata.custom?.sourcecadoState;
  const runBudget = message.metadata.custom?.sourcecadoRunBudget as
    | RunBudgetStatus
    | undefined;
  const notice = message.metadata.custom?.sourcecadoNotice === true;
  const frame = notice
    ? noticeFrame(message.metadata.custom?.sourcecadoNoticeCode)
    : null;
  const prose = message.content
    .filter((part) => part.type === "text")
    .map((part) => (part.type === "text" ? part.text : ""))
    .join("\n");
  const copyable =
    message.status?.type === "complete" && prose.trim().length > 0;
  const tools = message.content.filter(
    (part): part is ToolCallMessagePart => part.type === "tool-call",
  );
  const artifacts = Array.isArray(
    message.metadata.custom?.sourcecadoArtifacts,
  )
    ? (message.metadata.custom?.sourcecadoArtifacts as Array<
        Record<string, unknown>
      >)
    : [];

  return (
    <MessagePrimitive.Root
      className={`sourcecado-assistant-message${notice ? " sourcecado-notice" : ""}`}
      {...(notice ? { role: "note" } : {})}
    >
      {frame ? (
        <p className="sourcecado-notice-title">{frame.title}</p>
      ) : null}
      <MessagePrimitive.Parts
        components={{
          Text: AssistantText,
          Source: SourceCitation,
          tools: { Fallback: SafeToolFallback },
        }}
      />
      <ActivityGroup tools={tools} messageState={sourcecadoState} />
      <RunBudgetView status={runBudget} messageState={sourcecadoState} />
      {artifacts.length > 0 ? <ArtifactControls artifacts={artifacts} /> : null}
      {sourcecadoState === "cancelled" ? (
        <p className="sourcecado-terminal-receipt">Run cancelled.</p>
      ) : null}
      {frame?.detail ? (
        <p className="sourcecado-notice-detail">{frame.detail}</p>
      ) : null}
      {copyable ? (
        <button
          type="button"
          className="sourcecado-copy-action"
          onClick={() => void navigator.clipboard?.writeText(prose)}
        >
          Copy response
        </button>
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
