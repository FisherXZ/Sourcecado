import type { ToolCallMessagePart } from "@assistant-ui/react";
import { useId, useState } from "react";

import { useInspector } from "./Inspector";
import { ActionIcon, ToolIcon } from "./ProcessIcons";
import { formatElapsed, layerElapsedMs, layerState, type StepState } from "./processState";
import { StepStatus } from "./StepRow";
import { toolPresentation } from "./toolRegistry";
import {
  inspectableResult,
  resultsLabel,
  webQueryOf,
  webResultTarget,
  type WebQuery,
  type WebResult,
} from "./webSearch";

function ResultRow({ result }: { readonly result: WebResult }) {
  const { select } = useInspector();
  // The domain is text, never a link. The inspector holds the external link,
  // and it only draws one for a safe http(s) URL.
  const domain = result.domain ?? (result.url ? "Link not safe" : "No link");
  const flags = result.flags.length > 0 ? `, ${result.flags.join(", ")}` : "";
  return (
    <li>
      <button
        type="button"
        className="sourcecado-search-result"
        aria-label={`Result: ${result.title} (${domain})${flags}`}
        onClick={(event) => select(webResultTarget(result), event.currentTarget)}
      >
        <ToolIcon name="web" />
        <span className="sourcecado-search-result-title">{result.title}</span>
        <span className="sourcecado-search-result-meta">
          <span className="sourcecado-search-result-domain">{domain}</span>
          {result.flags.map((flag) => (
            <span key={flag} className="sourcecado-search-flag">
              {flag}
            </span>
          ))}
        </span>
      </button>
    </li>
  );
}

function SearchQuery({
  tool,
  query,
  open: initiallyOpen,
}: {
  readonly tool: ToolCallMessagePart;
  readonly query: WebQuery;
  readonly open: boolean;
}) {
  const { select } = useInspector();
  const [chosen, setChosen] = useState<boolean | null>(null);
  const open = chosen ?? initiallyOpen;
  const bodyId = useId();
  const text = query.query || "No query text";
  const count = query.results ? resultsLabel(query.results.length) : null;

  // A query that is still running has no results to open yet.
  if (query.state === "running") {
    return (
      <li className="sourcecado-search-query" data-tool-call-id={query.toolCallId}>
        <div className="sourcecado-search-query-line">
          <ActionIcon name="search" />
          <span className="sourcecado-search-query-text">{text}</span>
          <StepStatus state="running" />
        </div>
      </li>
    );
  }
  return (
    <li className="sourcecado-search-query" data-tool-call-id={query.toolCallId}>
      <button
        type="button"
        className="sourcecado-search-query-line"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={() => setChosen(!open)}
      >
        <ActionIcon name="search" />
        <span className="sourcecado-search-query-text">{text}</span>
        {query.state === "done" ? (
          count ? <span className="sourcecado-step-count">{count}</span> : null
        ) : (
          <StepStatus state={query.state} count={count} />
        )}
        <ActionIcon name="chevron" />
      </button>
      {open ? (
        <div id={bodyId} className="sourcecado-search-body">
          {query.results === null ? (
            <p>No result list is saved for this query.</p>
          ) : query.results.length === 0 ? (
            <p>The search returned no results.</p>
          ) : (
            <ul className="sourcecado-search-results" aria-label={`Results for ${text}`}>
              {query.results.map((result) => (
                <ResultRow key={result.id} result={result} />
              ))}
            </ul>
          )}
          <div className="sourcecado-step-actions">
            <button
              type="button"
              className="sourcecado-inspect-tool"
              aria-label={`Inspect search for ${text}`}
              title="Inspect"
              onClick={(event) =>
                select(
                  {
                    kind: "tool",
                    id: tool.toolCallId,
                    title: toolPresentation(tool.toolName).label,
                    status: "success",
                    args: tool.args,
                    result: inspectableResult(tool),
                    timing: tool.timing,
                  },
                  event.currentTarget,
                )
              }
            >
              <ActionIcon name="inspect" />
            </button>
          </div>
        </div>
      ) : null}
    </li>
  );
}

/**
 * One row in the process layer for a run of web searches: "Searched the web".
 * It opens to each query, and a query opens to the results the backend kept
 * for it. Retrieved results show here and nowhere else. Only the ones the
 * answer links become source chips under the answer.
 */
export function WebSearchRow({
  tools,
  states,
  now,
}: {
  readonly tools: readonly ToolCallMessagePart[];
  readonly states: readonly StepState[];
  readonly now: number;
}) {
  const [chosen, setChosen] = useState<boolean | null>(null);
  const open = chosen ?? false;
  const detailId = useId();
  const presentation = toolPresentation("web_search");
  const queries = tools.map((tool, index) => webQueryOf(tool, states[index]!));
  const state = layerState(states);
  const finished = state === "done" || state === "partial";
  const known = queries.flatMap((query) => (query.results ? [query.results.length] : []));
  const total = known.reduce((sum, size) => sum + size, 0);
  const only = queries.length === 1 ? queries[0]!.query : "";
  const elapsedMs = state === "running" ? layerElapsedMs(tools, states, now) : null;

  return (
    <li
      className={`sourcecado-step sourcecado-step-${state} sourcecado-search`}
      data-tool-call-id={tools[0]!.toolCallId}
    >
      <button
        type="button"
        className="sourcecado-step-toggle"
        aria-expanded={open}
        aria-controls={detailId}
        onClick={() => setChosen(!open)}
      >
        <ToolIcon name="web" />{" "}
        <span className="sourcecado-step-label">
          <span className="sourcecado-step-verb">
            {state === "running" ? presentation.activeLabel : presentation.label}
          </span>
          {only ? (
            <>
              {" "}
              <span className="sourcecado-step-object">for “{only}”</span>
            </>
          ) : queries.length > 1 ? (
            <>
              {" "}
              <span className="sourcecado-step-object">{queries.length} queries</span>
            </>
          ) : null}
        </span>{" "}
        <StepStatus
          state={state}
          count={finished && known.length > 0 ? resultsLabel(total) : null}
          elapsed={elapsedMs !== null ? formatElapsed(elapsedMs, true) : null}
        />{" "}
        <ActionIcon name="chevron" />
      </button>
      {open ? (
        <div id={detailId} className="sourcecado-search-detail">
          <ul className="sourcecado-search-queries" aria-label="Queries">
            {queries.map((query, index) => (
              <SearchQuery
                key={query.toolCallId}
                tool={tools[index]!}
                query={query}
                open={queries.length === 1}
              />
            ))}
          </ul>
        </div>
      ) : null}
    </li>
  );
}
