import type { SourceMessagePartProps } from "@assistant-ui/react";

import { useInspector } from "./Inspector";

export function SourceCitation(part: SourceMessagePartProps) {
  const { select } = useInspector();
  const title = part.title ?? "Source";
  const metadata = part.providerMetadata?.sourcecado as
    | Record<string, unknown>
    | undefined;
  return (
    <button
      type="button"
      className="sourcecado-source-citation"
      aria-label={`Source: ${title}`}
      onClick={(event) =>
        select(
          {
            kind: "source",
            id: part.id,
            title,
            status: "success",
            provider:
              typeof metadata?.provider === "string"
                ? metadata.provider
                : "Source",
            externalUrl: part.sourceType === "url" ? part.url : null,
            stale: metadata?.stale === true,
            truncated: metadata?.truncated === true,
          },
          event.currentTarget,
        )
      }
    >
      {title}
    </button>
  );
}
