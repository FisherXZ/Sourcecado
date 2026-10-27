import type { SourceMessagePart } from "@assistant-ui/react";
import { useId, useState } from "react";

import { Glyph } from "../generative/ResultCard";
import { useInspector } from "./Inspector";
import { safeExternalUrl } from "./inspectorFormat";
import { ToolIcon } from "./ProcessIcons";
import type { ToolIconKey } from "./toolRegistry";
import { webResultTarget, type WebResult } from "./webSearch";

const VISIBLE_CHIPS = 8;

const PROVIDER_ICONS: Readonly<Record<string, ToolIconKey>> = {
  apollo: "apollo",
  gmail: "gmail",
  "google drive": "drive",
  "google calendar": "calendar",
  granola: "granola",
  board: "person",
};

function providerOf(part: SourceMessagePart): string {
  const metadata = part.providerMetadata?.sourcecado as
    | Record<string, unknown>
    | undefined;
  return typeof metadata?.provider === "string" ? metadata.provider : "Source";
}

/**
 * The domain a source came from: the host of its URL without "www.". A source
 * with no URL (a Drive file, a note) shows its provider instead. Sourcecado
 * does not fetch a favicon. That request would tell the site that the
 * operator read it, so the chip draws a provider icon from the app itself.
 */
export function sourceDomain(part: SourceMessagePart): string {
  if (part.sourceType === "url") {
    try {
      return new URL(part.url).hostname.replace(/^www\./, "") || providerOf(part);
    } catch {
      return providerOf(part);
    }
  }
  return providerOf(part);
}

function isWebResult(value: unknown): value is WebResult {
  const candidate = value as Partial<WebResult> | null | undefined;
  return (
    typeof candidate === "object" &&
    candidate !== null &&
    typeof candidate.id === "string" &&
    typeof candidate.title === "string" &&
    Array.isArray(candidate.flags)
  );
}

function SourceChip({ part }: { readonly part: SourceMessagePart }) {
  const { select } = useInspector();
  const title = part.title || "Source";
  const provider = providerOf(part);
  const domain = sourceDomain(part);
  const metadata = part.providerMetadata?.sourcecado as
    | Record<string, unknown>
    | undefined;
  const stale = metadata?.stale === true;
  const truncated = metadata?.truncated === true;
  // A cited web result carries the retained result it came from (#252). The
  // chip opens that result and shows its states. It never fetches the page.
  const web = isWebResult(metadata?.web) ? metadata.web : null;
  const flags = [
    ...(stale ? ["Stale"] : []),
    ...(truncated ? ["Truncated"] : []),
    ...(web ? web.flags.filter((flag) => flag !== "Truncated") : []),
  ];
  const icon: ToolIconKey =
    PROVIDER_ICONS[provider.toLowerCase()] ??
    (part.sourceType === "url" ? "web" : "file");
  return (
    <button
      type="button"
      className="sourcecado-source-chip"
      aria-label={`Source: ${title} (${domain})${flags.length > 0 ? `, ${flags.join(", ")}` : ""}`}
      title={title}
      onClick={(event) =>
        select(
          web
            ? webResultTarget(web)
            : {
                kind: "source",
                id: part.id,
                title,
                status: "success",
                provider,
                externalUrl: part.sourceType === "url" ? part.url : null,
                // An address with a password in it is not shown, as text or as a link.
                preview: part.sourceType === "url" ? safeExternalUrl(part.url) : null,
                stale,
                truncated,
              },
          event.currentTarget,
        )
      }
    >
      <ToolIcon name={icon} />
      <span className="sourcecado-source-domain">{domain}</span>
      {flags.map((flag) => (
        <span key={flag} className="sourcecado-source-flag">
          {flag}
        </span>
      ))}
    </button>
  );
}

/**
 * The sources a turn cited, as a wrapping row of chips under the answer. A
 * click opens the source in the inspector, where the external link lives.
 */
export function SourceChips({
  sources,
}: {
  readonly sources: readonly SourceMessagePart[];
}) {
  const [expanded, setExpanded] = useState(false);
  const listId = useId();
  if (sources.length === 0) return null;
  const shown = expanded ? sources : sources.slice(0, VISIBLE_CHIPS);
  const hidden = sources.length - shown.length;
  // The toggle stays mounted when the row opens, so focus stays on it. It then
  // reads "Show fewer".
  return (
    <ul id={listId} className="sourcecado-source-chips" aria-label="Sources">
      {shown.map((part) => (
        <li key={part.id}>
          <SourceChip part={part} />
        </li>
      ))}
      {sources.length > VISIBLE_CHIPS ? (
        <li>
          <button
            type="button"
            className="sourcecado-source-chip sourcecado-source-more"
            aria-controls={listId}
            aria-expanded={expanded}
            data-open={expanded}
            onClick={() => setExpanded((open) => !open)}
          >
            <Glyph name="chevron" />
            <span>{expanded ? "Show fewer" : `Show ${hidden} more`}</span>
          </button>
        </li>
      ) : null}
    </ul>
  );
}
