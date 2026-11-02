import type { ToolCallMessagePart } from "@assistant-ui/react";
import { useId, useState } from "react";

import { failureOf } from "../chat/processState";
import { cardAnchorId } from "../chat/resultCards";
import type { ToolIconKey } from "../chat/toolRegistry";
import {
  CardBadge,
  CardButton,
  CardRows,
  ExpandableText,
  InspectButton,
  ResultCard,
  SkeletonRows,
} from "./ResultCard";
import { useInspector } from "../chat/Inspector";

const EXCERPT_PREVIEW_LIMIT = 280;
const PAGE_SIZE = 5;

type EvidenceItem = {
  readonly key: string;
  readonly sourceId: string;
  readonly provider: "Google Drive" | "Granola";
  readonly title: string;
  readonly excerpt: string | null;
  readonly context: string;
  readonly externalUrl: string | null;
  readonly stale: boolean;
  readonly truncated: boolean;
  readonly extractionStatus: string | null;
  readonly callIds: readonly string[];
};

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function failedProvider(
  tool: ToolCallMessagePart,
): "Google Drive" | "Granola" | null {
  const metadata = tool.providerMetadata?.sourcecado as
    | Record<string, unknown>
    | undefined;
  const failed = tool.isError || (metadata?.failure && typeof metadata.failure === "object");
  if (!failed) return null;
  return tool.toolName.startsWith("mcp__granola__") ? "Granola" : "Google Drive";
}

function providerForTool(tool: ToolCallMessagePart): "Google Drive" | "Granola" {
  return tool.toolName.startsWith("mcp__granola__") ? "Granola" : "Google Drive";
}

function malformedEvidenceTool(tool: ToolCallMessagePart): boolean {
  if (tool.result === undefined || failedProvider(tool)) return false;
  if (tool.toolName === "drive_search" || tool.toolName === "drive_list_folder") {
    const result = record(tool.result);
    return !result || !Array.isArray(result.files);
  }
  if (tool.toolName === "drive_read") {
    const result = record(tool.result);
    return !result ||
      !(text(result.id) || text(result.name) || text(result.content));
  }
  if (tool.toolName.startsWith("mcp__granola__")) {
    if (text(tool.result)) return false;
    const result = record(tool.result);
    if (!result) return true;
    return ![
      result.meetings,
      result.notes,
      result.transcripts,
      result.items,
      result.results,
    ].some(Array.isArray) && !text(result.result);
  }
  return false;
}

function driveItems(tool: ToolCallMessagePart): EvidenceItem[] {
  const result = record(tool.result);
  if (tool.toolName === "drive_search" || tool.toolName === "drive_list_folder") {
    const files = Array.isArray(result?.files) ? result.files : [];
    return files.flatMap((value, index) => {
      const file = record(value);
      if (!file) return [];
      const sourceId = text(file.id) ?? `${tool.toolCallId}:file:${index + 1}`;
      const mime = text(file.mimeType);
      return [{
        key: `drive:${sourceId}`,
        sourceId,
        provider: "Google Drive" as const,
        title: text(file.name) ?? "Untitled Drive file",
        excerpt: text(file.excerpt) ?? text(file.snippet),
        context: [
          tool.toolName === "drive_list_folder" ? "Folder child" : "Search match",
          mime,
        ].filter(Boolean).join(" · "),
        externalUrl:
          text(file.webViewLink) ?? text(file.url) ?? text(file.external_url),
        stale: file.stale === true,
        truncated: file.truncated === true,
        extractionStatus: null,
        callIds: [tool.toolCallId],
      }];
    });
  }
  if (tool.toolName === "drive_read" && result) {
    const sourceId = text(result.id) ?? text(record(tool.args)?.file_id) ?? tool.toolCallId;
    const mime = text(result.mimeType);
    const extractionStatus = text(result.status);
    const action = extractionStatus === "metadata_only"
      ? "Metadata only"
      : extractionStatus === "unsupported"
        ? "Unsupported format"
        : extractionStatus === "failed"
          ? "Read failed"
          : "Read document";
    return [{
      key: `drive:${sourceId}`,
      sourceId,
      provider: "Google Drive",
      title: text(result.name) ?? "Drive document",
      excerpt: text(result.content),
      context: [action, mime].filter(Boolean).join(" · "),
      externalUrl:
        text(result.webViewLink) ?? text(result.url) ?? text(result.external_url),
      stale: result.stale === true,
      truncated: result.truncated === true,
      extractionStatus,
      callIds: [tool.toolCallId],
    }];
  }
  return [];
}

function granolaItems(tool: ToolCallMessagePart): EvidenceItem[] {
  const result = record(tool.result);
  const collections = [
    result?.meetings,
    result?.notes,
    result?.transcripts,
    result?.items,
    result?.results,
  ];
  const values = collections.find(Array.isArray) as unknown[] | undefined;
  if (values) {
    return values.flatMap((value, index) => {
      const item = record(value);
      if (!item) return [];
      const sourceId = text(item.id) ?? `${tool.toolCallId}:item:${index + 1}`;
      return [{
        key: `granola:${sourceId}`,
        sourceId,
        provider: "Granola" as const,
        title: text(item.title) ?? text(item.name) ?? "Granola meeting context",
        excerpt:
          text(item.excerpt) ??
          text(item.summary) ??
          text(item.content) ??
          text(item.transcript) ??
          text(item.text),
        context: text(item.type) ?? "Meeting context",
        externalUrl:
          text(item.url) ?? text(item.htmlLink) ?? text(item.external_url),
        stale: item.stale === true,
        truncated: item.truncated === true,
        extractionStatus: null,
        callIds: [tool.toolCallId],
      }];
    });
  }
  const generic = text(result?.result) ?? text(tool.result);
  return generic
    ? [{
        key: `granola:${tool.toolCallId}`,
        sourceId: tool.toolCallId,
        provider: "Granola",
        title: "Granola meeting context",
        excerpt: generic,
        context: "Meeting context",
        externalUrl: null,
        stale: false,
        truncated: false,
        extractionStatus: null,
        callIds: [tool.toolCallId],
      }]
    : [];
}

function evidenceItems(tools: readonly ToolCallMessagePart[]): EvidenceItem[] {
  const byKey = new Map<string, EvidenceItem>();
  for (const tool of tools) {
    if (failedProvider(tool)) continue;
    const incoming = tool.toolName.startsWith("mcp__granola__")
      ? granolaItems(tool)
      : driveItems(tool);
    for (const item of incoming) {
      const current = byKey.get(item.key);
      byKey.set(
        item.key,
        current
          ? {
              ...current,
              title:
                current.title.startsWith("Untitled") || current.title === "Drive document"
                  ? item.title
                  : current.title,
              excerpt: item.excerpt ?? current.excerpt,
              context: [current.context, item.context]
                .filter((value, index, values) => values.indexOf(value) === index)
                .join(" · "),
              externalUrl: item.externalUrl ?? current.externalUrl,
              stale: current.stale || item.stale,
              truncated: current.truncated || item.truncated,
              extractionStatus: item.extractionStatus ?? current.extractionStatus,
              callIds: [...current.callIds, ...item.callIds],
            }
          : item,
      );
    }
  }
  return [...byKey.values()];
}

function iconFor(providers: readonly string[]): ToolIconKey {
  if (providers.length !== 1) return "file";
  return providers[0] === "Granola" ? "granola" : "drive";
}

export function EvidenceSetResult({
  tools,
}: {
  readonly tools: readonly ToolCallMessagePart[];
}) {
  const { select } = useInspector();
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);
  const listId = useId();
  const items = evidenceItems(tools);
  const toolCallIds = tools.map((tool) => tool.toolCallId);
  const cardId = cardAnchorId(toolCallIds[0] ?? "evidence");
  const loading = tools.some(
    (tool) => tool.result === undefined && failedProvider(tool) === null,
  );
  const providers = [...new Set(items.map((item) => item.provider))];
  const failedTools = tools.filter((tool) => failedProvider(tool) !== null);
  const failedProviders = [
    ...new Set(
      failedTools
        .map(failedProvider)
        .filter((provider): provider is "Google Drive" | "Granola" => provider !== null),
    ),
  ];
  const sourceProviders = [...new Set(tools.map(providerForTool))];
  const common = { id: cardId, toolCallIds, provider: iconFor(sourceProviders) };
  if (loading && items.length === 0) {
    return (
      <ResultCard {...common} title="Evidence" state="loading">
        <SkeletonRows label="Loading evidence" />
      </ResultCard>
    );
  }
  const malformed = tools.some(malformedEvidenceTool);
  if (items.length === 0 && malformed) {
    return (
      <ResultCard
        {...common}
        title="Evidence"
        state="unsupported"
        note="Sourcecado cannot read this result. The raw record is in the inspector."
        footer={
          <InspectButton
            target={{
              kind: "tool",
              id: toolCallIds.join("+"),
              title: "Evidence",
              status: "success",
              args: tools.map((tool) => tool.args),
              result: tools.map((tool) => tool.result),
            }}
          />
        }
      />
    );
  }
  if (items.length === 0 && failedProviders.length > 0) {
    const restricted = failedTools.every((tool) => failureOf(tool)?.class === "permission");
    return (
      <ResultCard
        {...common}
        title="Evidence"
        state={restricted ? "restricted" : "failed"}
        note={`${failedProviders.join(" and ")} evidence unavailable.`}
      />
    );
  }
  if (items.length === 0) {
    const queries = [
      ...new Set(
        tools
          .map((tool) => {
            const args = record(tool.args);
            return text(args?.query) ?? text(args?.q) ?? text(args?.search);
          })
          .filter((query): query is string => query !== null),
      ),
    ];
    return (
      <ResultCard
        {...common}
        title="Evidence"
        count="0 evidence items"
        state="empty"
        note={`No evidence matched ${queries.length > 0 ? queries.join(" or ") : "this request"} across ${sourceProviders.join(" and ")}.`}
      />
    );
  }
  const staleCount = items.filter((item) => item.stale).length;
  const partial = failedProviders.length > 0;
  const state = partial ? "partial" : staleCount > 0 ? "stale" : "ready";
  const note = partial
    ? failedProviders
        .map(
          (provider) =>
            `${provider} evidence unavailable. ${providers.join(" and ")} evidence remains visible.`,
        )
        .join(" ")
    : staleCount > 0
      ? staleCount === items.length
        ? items.length === 1
          ? "This item is a cached copy and may be out of date."
          : `All ${items.length} items are cached and may be out of date.`
        : `${staleCount} of ${items.length} items are cached and may be out of date.`
      : undefined;
  return (
    <ResultCard
      {...common}
      provider={iconFor(providers)}
      title="Evidence set"
      count={`${items.length} evidence ${items.length === 1 ? "item" : "items"}`}
      meta={providers}
      state={state}
      note={note}
      footer={
        <>
          <InspectButton
            target={{
              kind: "source",
              id: `evidence-set:${toolCallIds.join("+")}`,
              title: "Evidence set",
              status: "success",
              provider: providers.join(" and "),
              preview: `${items.length} evidence items from ${providers.join(" and ")}`,
              result: { callIds: toolCallIds },
            }}
          />
          {items.length > PAGE_SIZE ? (
            <CardButton
              icon="chevron"
              aria-controls={listId}
              aria-expanded={visibleCount > PAGE_SIZE}
              data-open={visibleCount >= items.length}
              onClick={() =>
                setVisibleCount(
                  visibleCount >= items.length
                    ? PAGE_SIZE
                    : Math.min(visibleCount + PAGE_SIZE, items.length),
                )
              }
            >
              {visibleCount >= items.length
                ? "Show fewer evidence items"
                : `Show ${Math.min(PAGE_SIZE, items.length - visibleCount)} more evidence items`}
            </CardButton>
          ) : null}
        </>
      }
    >
      <CardRows label="Evidence items" id={listId}>
        {items.slice(0, visibleCount).map((item) => (
          <li
            key={item.key}
            className="sourcecado-card-row sourcecado-card-row-stack"
            data-source-id={item.sourceId}
          >
            <div className="sourcecado-card-line">
              <span className="sourcecado-card-cell sourcecado-card-cell-name">
                <button
                  type="button"
                  className="sourcecado-card-name"
                  aria-label={`Inspect evidence ${item.title}`}
                  onClick={(event) =>
                    select(
                      {
                        kind: "source",
                        id: `evidence:${item.callIds.join("+")}:${item.sourceId}`,
                        title: item.title,
                        status: "success",
                        provider: item.provider,
                        externalUrl: item.externalUrl,
                        preview: item.excerpt
                          ? item.excerpt.slice(0, EXCERPT_PREVIEW_LIMIT)
                          : item.context,
                        stale: item.stale,
                        truncated: item.truncated,
                        result: {
                          sourceId: item.sourceId,
                          callIds: item.callIds,
                          context: item.context,
                          extractionStatus: item.extractionStatus,
                        },
                      },
                      event.currentTarget,
                    )
                  }
                >
                  {item.title}
                </button>
              </span>
              <span className="sourcecado-card-cell">{item.provider}</span>
              <span className="sourcecado-card-cell sourcecado-card-cell-context">
                {item.context}
              </span>
              {item.stale ? <CardBadge>Cached stale</CardBadge> : null}
              {item.truncated ? <CardBadge>Truncated</CardBadge> : null}
              {item.extractionStatus === "metadata_only" ? (
                <CardBadge>Metadata only</CardBadge>
              ) : null}
              {item.extractionStatus === "unsupported" ? (
                <CardBadge>Unsupported format</CardBadge>
              ) : null}
              {item.extractionStatus === "failed" ? (
                <CardBadge tone="error">Read failed</CardBadge>
              ) : null}
            </div>
            {item.excerpt ? (
              <ExpandableText
                text={item.excerpt}
                limit={EXCERPT_PREVIEW_LIMIT}
                expandLabel="Expand evidence excerpt"
                collapseLabel="Collapse evidence excerpt"
                className="sourcecado-card-body-text"
              />
            ) : null}
          </li>
        ))}
      </CardRows>
    </ResultCard>
  );
}
