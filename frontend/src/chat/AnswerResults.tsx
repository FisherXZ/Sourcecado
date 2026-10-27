import type { ToolCallMessagePart } from "@assistant-ui/react";

import { failureOf, isWaitingForApproval } from "./processState";
import { domainRendererFor, isEvidenceToolName } from "./toolRegistry";
import { EvidenceSetResult } from "../generative/EvidenceSetResult";

function domainStatusOf(tool: ToolCallMessagePart): "loading" | "success" | "error" {
  return failureOf(tool) || tool.isError
    ? "error"
    : tool.result === undefined
      ? "loading"
      : "success";
}

/**
 * Domain results the operator acts on: people, drafts, events, evidence.
 * They render in the answer area, below the answer text and outside the
 * process layer, so they stay visible when the layer is collapsed. The step
 * rows keep only the raw output for each call.
 */
export function AnswerResults({
  tools,
}: {
  readonly tools: readonly ToolCallMessagePart[];
}) {
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
