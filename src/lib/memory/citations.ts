import type { RunTrace, RunStepTrace } from "../ledger";
import type { MemoryBundle } from "./retrieve";

const CITATION_PATTERN = "[A-Za-z0-9._-]+#(?:chunk|row)-\\d+";

/** Walk a run trace and collect every MemoryBundle from search_memory tool calls. */
export function collectBundlesFromTrace(trace: RunTrace | null): MemoryBundle[] {
  if (!trace) return [];
  const bundles: MemoryBundle[] = [];
  function walk(steps: RunStepTrace[]) {
    for (const step of steps) {
      for (const tc of step.toolCalls) {
        if (tc.toolName === "search_memory" && tc.status === "succeeded" && tc.result) {
          bundles.push(tc.result as MemoryBundle);
        }
      }
      walk(step.children);
    }
  }
  walk(trace.steps);
  return bundles;
}

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

/**
 * Full citation post-check pipeline: walk the trace → collect allowed citations
 * → strip any citation tokens the agent invented. Returns the sanitized answer
 * and the list of invalid citation ids.
 */
export function verifyAnswerCitations(
  trace: RunTrace | null,
  answer: string
): { answer: string; invalidCitations: string[] } {
  const bundles = collectBundlesFromTrace(trace);
  const allowed = collectAllowedCitations(bundles);
  const { sanitizedAnswer, invalid } = checkCitations(answer, allowed);
  return { answer: sanitizedAnswer, invalidCitations: invalid };
}
