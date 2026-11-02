import type { SourceMessagePart, ToolCallMessagePart } from "@assistant-ui/react";

import { failureOf, isWaitingForApproval, stepState } from "./processState";
import { cardAnchorId } from "./resultCards";
import { SourceChips } from "./SourceCitation";
import { domainRendererFor, isEvidenceToolName, toolPresentation } from "./toolRegistry";
import { citedWebSources, samePage } from "./webSearch";
import { EvidenceSetResult } from "../generative/EvidenceSetResult";
import { CardBoundary, rawToolTarget, UnsupportedCard } from "../generative/ResultCard";

function domainStatusOf(tool: ToolCallMessagePart): "loading" | "success" | "error" {
  return failureOf(tool) || tool.isError
    ? "error"
    : tool.result === undefined
      ? "loading"
      : "success";
}

/**
 * A step with no recorded result after the turn ended (it was interrupted, or
 * its outcome is unknown) has nothing to show. A card would read "Loading"
 * forever, so it gets no card. The step row says what happened.
 */
function hasNothingToShow(tool: ToolCallMessagePart, messageState: unknown): boolean {
  const state = stepState(tool, messageState);
  return state === "unknown" || state === "interrupted";
}

/**
 * What a turn leaves for the operator, below the answer text and outside the
 * process layer, so it stays visible when the layer is collapsed: the result
 * cards (people, drafts, events, briefs, evidence) and then the source chips.
 * The step rows keep only the raw output for each call.
 */
export function AnswerResults({
  tools,
  sources = [],
  messageState,
  answerText = "",
}: {
  readonly tools: readonly ToolCallMessagePart[];
  readonly sources?: readonly SourceMessagePart[];
  /** The turn's state. It tells a running call from one that never finished. */
  readonly messageState?: unknown;
  /** The answer text. A web result the answer links becomes a source chip. */
  readonly answerText?: string;
}) {
  const shown = tools.filter((tool) => !hasNothingToShow(tool, messageState));
  const evidenceTools = shown.filter((tool) => isEvidenceToolName(tool.toolName));
  const domainTools = shown.filter(
    (tool) =>
      !isEvidenceToolName(tool.toolName) &&
      domainRendererFor(tool.toolName) !== null &&
      !isWaitingForApproval(tool),
  );
  // Web results the answer links are cited sources. The rest stay in the search row.
  const chips = [
    ...sources,
    ...citedWebSources(tools, messageState, answerText).filter(
      (web) =>
        !sources.some(
          (source) =>
            source.sourceType === "url" && web.sourceType === "url" && samePage(source.url, web.url),
        ),
    ),
  ];
  if (domainTools.length === 0 && evidenceTools.length === 0 && chips.length === 0) {
    return null;
  }
  return (
    <div className="sourcecado-answer-results">
      {domainTools.map((tool) => {
        const DomainRenderer = domainRendererFor(tool.toolName);
        if (!DomainRenderer) return null;
        const cardId = cardAnchorId(tool.toolCallId);
        const presentation = toolPresentation(tool.toolName);
        const props = {
          toolCallId: tool.toolCallId,
          toolName: tool.toolName,
          args: tool.args,
          result: tool.result,
          status: domainStatusOf(tool),
          failure: failureOf(tool),
          cardId,
        } as const;
        return (
          <CardBoundary
            key={tool.toolCallId}
            fallback={() => (
              <UnsupportedCard
                cardId={cardId}
                toolCallIds={[tool.toolCallId]}
                provider={presentation.icon}
                title={presentation.label}
                inspect={rawToolTarget(props, presentation.label)}
              />
            )}
          >
            <DomainRenderer {...props} />
          </CardBoundary>
        );
      })}
      {evidenceTools.length > 0 ? (
        <CardBoundary
          fallback={() => (
            <UnsupportedCard
              cardId={cardAnchorId(evidenceTools[0]!.toolCallId)}
              toolCallIds={evidenceTools.map((tool) => tool.toolCallId)}
              provider="file"
              title="Evidence"
              inspect={{
                kind: "tool",
                id: evidenceTools.map((tool) => tool.toolCallId).join("+"),
                title: "Evidence",
                status: "success",
                args: evidenceTools.map((tool) => tool.args),
                result: evidenceTools.map((tool) => tool.result),
              }}
            />
          )}
        >
          <EvidenceSetResult tools={evidenceTools} />
        </CardBoundary>
      ) : null}
      <SourceChips sources={chips} />
    </div>
  );
}
