import type { ToolCallMessagePart } from "@assistant-ui/react";
import { useEffect, useId, useRef, useState } from "react";

import {
  domainRendererFor,
  isEvidenceToolName,
  toolPresentation,
} from "./toolRegistry";
import type { ToolFailure } from "./protocol";
import { useRecoveryActions } from "./recovery";
import { useInspector } from "./Inspector";
import { EvidenceSetResult } from "../generative/EvidenceSetResult";
import { sanitizeApolloNameMasks } from "../personName";

type ActivityState =
  | "Running"
  | "Completed"
  | "Failed"
  | "Denied"
  | "Partial"
  | "Interrupted";

function partState(part: ToolCallMessagePart): ActivityState {
  if (failureOf(part)) return "Failed";
  if (part.approval?.approved === false) return "Denied";
  if (part.approval?.resolution) return "Interrupted";
  if (part.isError) return "Failed";
  if (part.result !== undefined) return "Completed";
  return "Running";
}

function sourcecadoMetadata(part: ToolCallMessagePart): Record<string, unknown> {
  return (part.providerMetadata?.sourcecado as Record<string, unknown>) ?? {};
}

function failureOf(part: ToolCallMessagePart): ToolFailure | null {
  const failure = sourcecadoMetadata(part).failure;
  return failure && typeof failure === "object" ? (failure as ToolFailure) : null;
}

function recoveryOf(part: ToolCallMessagePart): Record<string, unknown> | null {
  const recovery = sourcecadoMetadata(part).recovery;
  return recovery && typeof recovery === "object"
    ? (recovery as Record<string, unknown>)
    : null;
}

function groupState(
  tools: readonly ToolCallMessagePart[],
  messageState: unknown,
): ActivityState {
  if (
    messageState === "cancelled" ||
    messageState === "stopped" ||
    messageState === "interrupted"
  ) {
    return "Interrupted";
  }
  const states = tools.map(partState);
  if (states.includes("Running")) return "Running";
  if (states.every((state) => state === "Denied")) return "Denied";
  if (states.every((state) => state === "Failed")) return "Failed";
  if (states.some((state) => state !== "Completed")) return "Partial";
  return "Completed";
}

function elapsedLabel(tools: readonly ToolCallMessagePart[], now: number): string | null {
  const starts = tools
    .map((tool) => tool.timing?.startedAt)
    .filter((value): value is number => value !== undefined);
  if (starts.length === 0) return null;
  const ends = tools
    .map((tool) => tool.timing?.completedAt)
    .filter((value): value is number => value !== undefined);
  const endAt = ends.length > 0 ? Math.max(...ends) : now;
  const seconds = Math.max(0, Math.round((endAt - Math.min(...starts)) / 1000));
  return `${seconds}s`;
}

function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return false;
  }
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

function isWaitingForApproval(tool: ToolCallMessagePart): boolean {
  return (
    tool.approval !== undefined &&
    tool.approval.approved !== true &&
    tool.result === undefined
  );
}

function domainStatusOf(tool: ToolCallMessagePart): "loading" | "success" | "error" {
  return failureOf(tool) || tool.isError
    ? "error"
    : tool.result === undefined
      ? "loading"
      : "success";
}

function AnswerResults({ tools }: { readonly tools: readonly ToolCallMessagePart[] }) {
  const evidenceTools = tools.filter((tool) => isEvidenceToolName(tool.toolName));
  const domainTools = tools.filter(
    (tool) =>
      !isEvidenceToolName(tool.toolName) &&
      domainRendererFor(tool.toolName) !== null &&
      !isWaitingForApproval(tool),
  );
  if (domainTools.length === 0 && evidenceTools.length === 0) return null;
  return (
    <div className="sourcecado-answer-results">
      {domainTools.map((tool) => {
        const DomainRenderer = domainRendererFor(tool.toolName);
        if (!DomainRenderer) return null;
        return (
          <DomainRenderer
            key={tool.toolCallId}
            toolCallId={tool.toolCallId}
            toolName={tool.toolName}
            args={tool.args}
            result={tool.result}
            status={domainStatusOf(tool)}
          />
        );
      })}
      {evidenceTools.length > 0 ? <EvidenceSetResult tools={evidenceTools} /> : null}
    </div>
  );
}

const WORKSPACE_RECEIPT_LABELS: Readonly<Record<string, string>> = {
  created: "Created workspace file",
  updated: "Updated workspace file",
  moved: "Moved workspace item",
  trashed: "Trashed workspace item",
  shell_approved: "Ran approved workspace command",
  shell_auto_read: "Ran read-only workspace command",
  denied: "Workspace action denied",
  interrupted: "Workspace action interrupted",
  stale: "Workspace write was stale",
  read: "Read workspace data",
  directory_requested: "Requested workspace access",
};

function workspaceReceiptLabel(tool: ToolCallMessagePart): string | null {
  const result = tool.result;
  if (!result || typeof result !== "object" || Array.isArray(result)) return null;
  const receiptType = (result as Record<string, unknown>).receipt_type;
  return typeof receiptType === "string"
    ? WORKSPACE_RECEIPT_LABELS[receiptType] ?? null
    : null;
}

function receiptLabel(tools: readonly ToolCallMessagePart[]): string {
  if (tools.length === 1) {
    const workspaceLabel = workspaceReceiptLabel(tools[0]);
    if (workspaceLabel) return workspaceLabel;
  }
  const presentations = tools.map((tool) => toolPresentation(tool.toolName));
  if (tools.length === 1) return presentations[0]?.label ?? "Ran Sourcecado action";
  if (presentations.every((item) => item.category === "source")) {
    return `Checked ${tools.length} ${tools.length === 1 ? "source" : "sources"}`;
  }
  return `Ran ${tools.length} actions`;
}

export function ActivityGroup({
  tools,
  messageState,
}: {
  readonly tools: readonly ToolCallMessagePart[];
  readonly messageState: unknown;
}) {
  const state = groupState(tools, messageState);
  const [expanded, setExpanded] = useState(state === "Running");
  const userToggledRef = useRef(false);
  const previousStateRef = useRef(state);
  const contentId = useId();
  const [reducedMotion] = useState(prefersReducedMotion);
  const [, tick] = useState(0);

  useEffect(() => {
    if (
      previousStateRef.current === "Running" &&
      state !== "Running" &&
      !userToggledRef.current
    ) {
      setExpanded(false);
    }
    previousStateRef.current = state;
  }, [state]);

  useEffect(() => {
    if (state !== "Running") return;
    const id = window.setInterval(
      () => tick((value) => value + 1),
      reducedMotion ? 5000 : 1000,
    );
    return () => window.clearInterval(id);
  }, [state, reducedMotion]);

  if (tools.length === 0) return null;
  const elapsed = elapsedLabel(tools, Date.now());
  const summary = [receiptLabel(tools), elapsed, state].filter(Boolean).join(" · ");
  return (
    <>
      <AnswerResults tools={tools} />
      <section className={`sourcecado-activity sourcecado-activity-${state.toLowerCase()}`}>
        <button
          type="button"
          aria-expanded={expanded}
          aria-controls={contentId}
          onClick={() => {
            userToggledRef.current = true;
            setExpanded((current) => !current);
          }}
        >
          {summary}
        </button>
        {expanded ? (
          <ol id={contentId}>
            {tools.map((tool) => (
              <ActivityRow key={tool.toolCallId} tool={tool} />
            ))}
          </ol>
        ) : null}
      </section>
    </>
  );
}

function ActivityRow({ tool }: { readonly tool: ToolCallMessagePart }) {
  const presentation = toolPresentation(tool.toolName);
  const failure = failureOf(tool);
  const recovery = recoveryOf(tool);
  const recoveryStatus = recovery?.status ? String(recovery.status) : null;
  const canRecover =
    failure !== null &&
    recoveryStatus !== "succeeded" &&
    recoveryStatus !== "skipped" &&
    recoveryStatus !== "denied" &&
    recoveryStatus !== "interrupted";
  const { act } = useRecoveryActions();
  const { select } = useInspector();
  const [showDetails, setShowDetails] = useState(false);
  const inspectorArgs = tool.toolName.startsWith("apollo_")
    ? sanitizeApolloNameMasks(tool.args)
    : tool.args;
  const inspectorResult = tool.toolName.startsWith("apollo_")
    ? sanitizeApolloNameMasks(tool.result)
    : tool.result;
  return (
    <li
      data-tool-call-id={tool.toolCallId}
      {...(presentation.slot ? { "data-renderer-slot": presentation.slot } : {})}
    >
      <div className="sourcecado-activity-row-copy">
        <span>{presentation.label}</span>
        {failure ? <p role="alert">{failure.summary}</p> : null}
        {recovery?.outcome ? <p>{String(recovery.outcome)}</p> : null}
        <button
          type="button"
          className="sourcecado-inspect-tool"
          aria-label={`Inspect ${presentation.label}`}
          onClick={(event) =>
            select(
              {
                kind: "tool",
                id: tool.toolCallId,
                title: presentation.label,
                status: "success",
                args: inspectorArgs,
                result: inspectorResult,
                timing: tool.timing,
              },
              event.currentTarget,
            )
          }
        >
          Inspect
        </button>
        {canRecover && failure ? (
          <div className="sourcecado-recovery-actions">
            <button type="button" onClick={() => act("retry", failure)}>
              Retry failed step
            </button>
            {failure.repair_route ? (
              <a
                href={failure.repair_route}
                onClick={() => act("repair", failure)}
              >
                Repair {failure.source}
              </a>
            ) : null}
            <button type="button" onClick={() => act("continue", failure)}>
              Continue without {failure.source}
            </button>
            <button
              type="button"
              aria-expanded={showDetails}
              onClick={() => setShowDetails((current) => !current)}
            >
              Failure details
            </button>
          </div>
        ) : null}
        {showDetails && failure ? (
          <pre className="sourcecado-failure-detail">{failure.detail}</pre>
        ) : null}
      </div>
      <strong>{partState(tool)}</strong>
    </li>
  );
}
