import type { ToolCallMessagePart } from "@assistant-ui/react";
import { useId, useState } from "react";

import { useInspector } from "../chat/Inspector";

const EXCERPT_PREVIEW_LIMIT = 280;

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

function EvidenceExcerpt({ excerpt }: { readonly excerpt: string }) {
  const [expanded, setExpanded] = useState(false);
  const excerptId = useId();
  const truncated = excerpt.length > EXCERPT_PREVIEW_LIMIT;
  const visible =
    truncated && !expanded
      ? `${excerpt.slice(0, EXCERPT_PREVIEW_LIMIT).trimEnd()}…`
      : excerpt;
  return (
    <div className="sourcecado-evidence-excerpt">
      <p id={excerptId}>{visible}</p>
      {truncated ? (
        <button
          type="button"
          aria-controls={excerptId}
          aria-expanded={expanded}
          onClick={() => setExpanded((current) => !current)}
        >
          {expanded ? "Collapse evidence excerpt" : "Expand evidence excerpt"}
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
          tool.toolName === "drive_list_folder" ? "Folder item" : "Search match",
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

export function EvidenceSetResult({
  tools,
}: {
  readonly tools: readonly ToolCallMessagePart[];
}) {
  const { select } = useInspector();
  const [visibleCount, setVisibleCount] = useState(5);
  const items = evidenceItems(tools);
  const loading = tools.some(
    (tool) => tool.result === undefined && failedProvider(tool) === null,
  );
  if (loading && items.length === 0) {
    return (
      <section className="sourcecado-evidence-set sourcecado-evidence-loading">
        <h3>Loading evidence</h3>
        <ol aria-label="Loading evidence" aria-busy="true">
          {[0, 1, 2].map((row) => (
            <li key={row}>
              <span />
              <span />
            </li>
          ))}
        </ol>
      </section>
    );
  }
  const providers = [...new Set(items.map((item) => item.provider))];
  const failedProviders = [
    ...new Set(
      tools
        .map(failedProvider)
        .filter((provider): provider is "Google Drive" | "Granola" => provider !== null),
    ),
  ];
  const sourceProviders = [...new Set(tools.map(providerForTool))];
  const malformed = tools.some(malformedEvidenceTool);
  if (items.length === 0 && malformed) {
    return (
      <section className="sourcecado-evidence-set sourcecado-evidence-fallback">
        <h3>Evidence result needs review</h3>
        <p>Sourcecado couldn’t summarize this evidence safely. Use Inspect above to review it.</p>
      </section>
    );
  }
  if (items.length === 0 && failedProviders.length > 0) {
    return (
      <section className="sourcecado-evidence-set sourcecado-evidence-unavailable">
        <h3>Evidence unavailable</h3>
        <p>{failedProviders.join(" and ")} evidence unavailable.</p>
      </section>
    );
  }
  if (items.length === 0 && failedProviders.length === 0) {
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
      <section className="sourcecado-evidence-set sourcecado-evidence-empty">
        <h3>No evidence found</h3>
        <p>
          No evidence matched {queries.length > 0 ? queries.join(" or ") : "this request"} across {sourceProviders.join(" and ")}.
        </p>
      </section>
    );
  }
  return (
    <section className="sourcecado-evidence-set" aria-label="Evidence set">
      <header>
        <div>
          <h3>Evidence set</h3>
          <p>{items.length} evidence items</p>
        </div>
        <p>{providers.join(" and ")}</p>
      </header>
      {failedProviders.length > 0 && items.length > 0 ? (
        <div className="sourcecado-evidence-partial">
          <strong>Partial evidence</strong>
          {failedProviders.map((provider) => (
            <p key={provider}>
              {provider} evidence unavailable. {providers.join(" and ")} evidence remains visible.
            </p>
          ))}
        </div>
      ) : null}
      <ol aria-label="Evidence items">
        {items.slice(0, visibleCount).map((item) => (
          <li key={item.key} data-source-id={item.sourceId}>
            <button
              type="button"
              className="sourcecado-evidence-title"
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
            <p>{item.provider}</p>
            <p>{item.context}</p>
            {item.stale ? <span className="sourcecado-evidence-badge">Cached stale</span> : null}
            {item.truncated ? <span className="sourcecado-evidence-badge">Truncated</span> : null}
            {item.extractionStatus === "metadata_only" ? (
              <span className="sourcecado-evidence-badge">Metadata only</span>
            ) : null}
            {item.extractionStatus === "unsupported" ? (
              <span className="sourcecado-evidence-badge">Unsupported format</span>
            ) : null}
            {item.excerpt ? <EvidenceExcerpt excerpt={item.excerpt} /> : null}
          </li>
        ))}
      </ol>
      {visibleCount < items.length ? (
        <button
          type="button"
          onClick={() =>
            setVisibleCount((count) => Math.min(count + 5, items.length))
          }
        >
          Show {Math.min(5, items.length - visibleCount)} more evidence items
        </button>
      ) : null}
    </section>
  );
}
