import type { MemoryBundle } from "./retrieve";

const CITATION_PATTERN = "[A-Za-z0-9._-]+#(?:chunk|row)-\\d+";

/** Collect every citation id the search_memory tool actually returned for a run. */
export function collectAllowedCitations(bundles: MemoryBundle[]): Set<string> {
  const allowed = new Set<string>();
  for (const bundle of bundles) {
    for (const fact of bundle.acceptedFacts) {
      if (fact.citation) allowed.add(fact.citation);
    }
    for (const fact of bundle.gapFacts) {
      if (fact.citation) allowed.add(fact.citation);
    }
    for (const chunk of bundle.chunks) {
      allowed.add(chunk.citation);
    }
  }
  return allowed;
}

/**
 * Find citation-like tokens in the answer and split into valid / invalid
 * against the allowed set. Invalid tokens are replaced in sanitizedAnswer.
 */
export function checkCitations(
  answer: string,
  allowed: Set<string>
): { invalid: string[]; sanitizedAnswer: string } {
  const cited = answer.match(new RegExp(CITATION_PATTERN, "g")) ?? [];
  const invalidSet = new Set(cited.filter((id) => !allowed.has(id)));
  const invalid = [...invalidSet];

  const sanitizedAnswer = answer.replace(
    new RegExp(CITATION_PATTERN, "g"),
    (match) => (invalidSet.has(match) ? "[unverified citation removed]" : match)
  );

  return { invalid, sanitizedAnswer };
}
