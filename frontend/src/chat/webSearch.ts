import type { SourceMessagePart, ToolCallMessagePart } from "@assistant-ui/react";

import type { InspectorTarget } from "./Inspector";
import { stepState, type StepState } from "./processState";
import { evidenceReferences, type EvidenceReference } from "./protocol";

/*
 * Web search keeps three levels apart.
 *
 *   query             one `web_search` call and the words it searched for
 *   retrieved result  one row that call returned and the backend retained
 *   cited source      a retrieved result the answer links to
 *
 * A retrieved result is not a citation. The backend records no citation, so
 * "cited" is read from the answer: the answer text links the result's URL.
 * Nothing here fetches a page. Every field comes from what the tool call
 * stored (the result rows and the evidence record), so opening a result never
 * changes the retained evidence.
 */

export const WEB_SEARCH_TOOL = "web_search";

export type WebResult = {
  /** The backend's evidence id when it sent one, else a stable local id. */
  readonly id: string;
  readonly title: string;
  /** The URL as retained. It may be unsafe. Only `domain` says it is a web link. */
  readonly url: string | null;
  /** Host of an http(s) URL without "www.", else null. */
  readonly domain: string | null;
  /** The retained snippet. Null when there is none, or the source is restricted. */
  readonly excerpt: string | null;
  /** Every state to show for this result, in the order the operator reads them. */
  readonly flags: readonly string[];
  readonly truncated: boolean;
  readonly query: string;
};

export type WebQuery = {
  readonly toolCallId: string;
  readonly query: string;
  readonly state: StepState;
  /** Null when the call kept no result list (an older saved copy). */
  readonly results: readonly WebResult[] | null;
};

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function evidenceOf(tool: ToolCallMessagePart): readonly EvidenceReference[] {
  const meta = record(tool.providerMetadata?.sourcecado);
  return evidenceReferences(meta?.evidence);
}

/** The host of an http(s) URL without "www.". Anything else is not a web link. */
export function webDomain(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const parsed = new URL(value);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return null;
    return parsed.hostname.replace(/^www\./, "") || null;
  } catch {
    return null;
  }
}

/**
 * The words the operator reads for each evidence state the backend can record.
 * `present` says nothing. An unknown value is shown as Unverified, so a state
 * this build does not know never reads as a clean result.
 */
const CONTENT_WORDS: Readonly<Record<string, string | null>> = {
  present: null,
  partial: "Partial",
  absent: "No excerpt",
  missing: "Missing",
  expired: "Expired",
  ambiguous: "Unverified",
  unsupported: "Unsupported",
};

function flagsOf(
  reference: EvidenceReference | undefined,
  hasExcerpt: boolean,
): string[] {
  const flags: string[] = [];
  if (reference) {
    if (reference.sensitivity === "restricted") flags.push("Restricted");
    else if (reference.sensitivity === "sensitive") flags.push("Sensitive");
    if (reference.truncated) flags.push("Truncated");
    const known = Object.prototype.hasOwnProperty.call(CONTENT_WORDS, reference.content);
    const word = known ? CONTENT_WORDS[reference.content] : "Unverified";
    if (word) flags.push(word);
  }
  if (!hasExcerpt && !flags.some((flag) => flag === "No excerpt" || flag === "Missing")) {
    flags.push("No excerpt");
  }
  return flags;
}

/**
 * The backend writes one evidence record per result row, in order. If the
 * counts differ the two cannot be lined up, and no state is guessed.
 */
function alignedEvidence(
  tool: ToolCallMessagePart,
  rowCount: number,
): readonly (EvidenceReference | undefined)[] {
  const evidence = evidenceOf(tool);
  return evidence.length === rowCount ? evidence : [];
}

function resultsOf(tool: ToolCallMessagePart, query: string): WebResult[] | null {
  const rows = record(tool.result)?.results;
  if (!Array.isArray(rows)) return null;
  const dicts = rows.filter((row): row is Record<string, unknown> => record(row) !== null);
  const evidence = alignedEvidence(tool, dicts.length);
  return dicts.map((row, index) => {
    const reference = evidence[index];
    const url = text(row.url) || null;
    const restricted = reference?.sensitivity === "restricted";
    const snippet = text(row.snippet);
    const excerpt = restricted || !snippet ? null : snippet;
    const domain = webDomain(url);
    return {
      id: reference?.id ?? `${tool.toolCallId}:result:${index}`,
      title: text(row.title) || reference?.title || domain || "Web result",
      url,
      domain,
      excerpt,
      flags: flagsOf(reference, restricted || snippet !== ""),
      truncated: reference?.truncated === true,
      query,
    };
  });
}

/**
 * The stored result for the raw Inspect view, with the snippet of a restricted
 * result taken out. The row, the chip and the inspector withhold that excerpt,
 * so the raw view must not hand it back.
 */
export function inspectableResult(tool: ToolCallMessagePart): unknown {
  const raw = record(tool.result);
  const rows = raw?.results;
  if (!raw || !Array.isArray(rows)) return tool.result;
  const evidence = alignedEvidence(tool, rows.filter((row) => record(row) !== null).length);
  if (!evidence.some((reference) => reference?.sensitivity === "restricted")) return tool.result;
  let next = 0;
  return {
    ...raw,
    results: rows.map((row) => {
      if (record(row) === null) return row;
      return evidence[next++]?.sensitivity === "restricted"
        ? { ...(row as Record<string, unknown>), snippet: "(restricted: not shown)" }
        : row;
    }),
  };
}

/**
 * The rows of the process layer. A run of adjacent `web_search` calls becomes
 * one search row. A call that needs the operator (failed, blocked, interrupted,
 * unknown) stays its own step row, so its recovery controls and its plain
 * state word keep working.
 */
export type StepItem =
  | { readonly kind: "step"; readonly index: number }
  | { readonly kind: "search"; readonly indexes: number[] };

const SEARCH_ROW_STATES: ReadonlySet<StepState> = new Set(["running", "done", "partial"]);

export function stepItems(
  tools: readonly ToolCallMessagePart[],
  states: readonly StepState[],
): StepItem[] {
  const items: StepItem[] = [];
  tools.forEach((tool, index) => {
    const inSearchRow =
      tool.toolName === WEB_SEARCH_TOOL && SEARCH_ROW_STATES.has(states[index]!);
    const last = items[items.length - 1];
    if (inSearchRow && last?.kind === "search") last.indexes.push(index);
    else items.push(inSearchRow ? { kind: "search", indexes: [index] } : { kind: "step", index });
  });
  return items;
}

/** One query for each `web_search` call, with its retained results. */
export function webQueryOf(tool: ToolCallMessagePart, state: StepState): WebQuery {
  const query = text(record(tool.args)?.query);
  return {
    toolCallId: tool.toolCallId,
    query,
    state,
    results: state === "done" || state === "partial" ? resultsOf(tool, query) : [],
  };
}

/** A URL reduced to what names the page: no scheme, "www.", fragment or trailing slash. */
function urlKey(value: string): string | null {
  try {
    const parsed = new URL(value);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return null;
    const host = parsed.hostname.replace(/^www\./, "");
    const path = parsed.pathname.replace(/\/+$/, "");
    return `${host}${parsed.port ? `:${parsed.port}` : ""}${path}${parsed.search}`;
  } catch {
    return null;
  }
}

/** True when two URLs name the same page. */
export function samePage(first: string, second: string): boolean {
  const key = urlKey(first);
  return key !== null && key === urlKey(second);
}

/** Takes off the punctuation that follows a link in prose, and a ")" with no "(". */
function trimUrl(raw: string): string {
  let url = raw;
  for (;;) {
    const last = url.slice(-1);
    const closesBracket =
      last === ")" && url.split(")").length > url.split("(").length;
    if (/[.,;:!?*_'"]/.test(last) || closesBracket) {
      url = url.slice(0, -1);
    } else {
      return url;
    }
  }
}

/** The pages an answer links to, as comparable keys. Markdown links and bare URLs both count. */
export function linkedPages(answer: string): ReadonlySet<string> {
  const keys = new Set<string>();
  // Square brackets end a match so `[https://a.com](https://a.com)` reads as two links.
  for (const match of answer.matchAll(/https?:\/\/[^\s<>"`\[\]]+/g)) {
    const key = urlKey(trimUrl(match[0]));
    if (key) keys.add(key);
  }
  return keys;
}

/**
 * The retrieved results the answer cites, once each, as source parts for the
 * chips under the answer. A result the answer does not link never appears
 * here. Each part carries the retained result, so the chip opens it without
 * fetching anything.
 */
export function citedWebSources(
  tools: readonly ToolCallMessagePart[],
  messageState: unknown,
  answer: string,
): SourceMessagePart[] {
  const linked = linkedPages(answer);
  if (linked.size === 0) return [];
  // A page can come back from more than one query. It is one source. If the
  // copies differ, the one with the most states wins: the answer does not say
  // which copy it used, so a flagged copy is never hidden behind a clean one.
  const cited = new Map<string, WebResult>();
  for (const tool of tools) {
    if (tool.toolName !== WEB_SEARCH_TOOL) continue;
    const { results } = webQueryOf(tool, stepState(tool, messageState));
    for (const result of results ?? []) {
      const key = result.url ? urlKey(result.url) : null;
      if (!key || !linked.has(key)) continue;
      const earlier = cited.get(key);
      if (!earlier || result.flags.length > earlier.flags.length) cited.set(key, result);
    }
  }
  return [...cited.values()].map((result) => ({
    type: "source",
    sourceType: "url",
    id: result.id,
    url: result.url!,
    title: result.title,
    providerMetadata: {
      sourcecado: {
        provider: "Web",
        stale: false,
        truncated: result.truncated,
        web: result,
      },
    },
  }));
}

/**
 * One plain sentence for each state, shown in the inspector ahead of the
 * excerpt. The inspector has a badge for Truncated only, so every other state
 * says itself in the text. A source with none of these opens with its excerpt.
 */
const STATE_NOTES: Readonly<Record<string, string>> = {
  Restricted: "Restricted source. The excerpt is not shown.",
  Sensitive: "Sensitive source.",
  Partial: "Partial source. Only part of it was retained.",
  Missing: "Missing source. The record of it has a gap.",
  Expired: "Expired source. Retention removed it.",
  Unverified: "Unverified source. The record does not say how much was kept.",
  Unsupported: "Unsupported source. This build cannot check it.",
  "No excerpt": "No excerpt was retained for this result.",
};

/**
 * The inspector item for a retained result. It reads the stored result only.
 * `externalUrl` is passed as retained, and the inspector decides whether it is
 * a safe link. A restricted result passes no excerpt, and a result with none
 * says why, so a source never opens as a blank panel.
 */
export function webResultTarget(result: WebResult): InspectorTarget {
  const states = result.flags.filter((flag) => flag !== "Truncated");
  const preview = [...states.map((flag) => STATE_NOTES[flag]), result.excerpt]
    .filter(Boolean)
    .join(" ");
  return {
    kind: "source",
    id: result.id,
    title: result.title,
    status: "success",
    provider: "Web",
    externalUrl: result.url,
    preview: preview || null,
    stale: false,
    truncated: result.truncated,
    result: {
      query: result.query,
      domain: result.domain,
      ...(states.length > 0 ? { state: states.join(", ") } : {}),
    },
  };
}

/** "5 results", "1 result". */
export function resultsLabel(count: number): string {
  return count === 0 ? "No results" : `${count} ${count === 1 ? "result" : "results"}`;
}
