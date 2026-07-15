import type { RunTrace, RunStepTrace } from "../ledger";
import type { MemoryBundle } from "./retrieve";

const CITATION_PATTERN = "[A-Za-z0-9._/-]+#(?:chunk|row)-\\d+";

/** Walk a run trace and collect every MemoryBundle from search_memory tool calls. */
export function collectBundlesFromTrace(
  trace: RunTrace | null,
  sinceStepId?: number
): MemoryBundle[] {
  if (!trace) return [];
  const bundles: MemoryBundle[] = [];
  function walk(steps: RunStepTrace[]) {
    for (const step of steps) {
      // Multi-turn chat sessions (R6) nest a fresh "agent" step per turn
      // under the same run; step ids are assigned sequentially, so this
      // scopes the walk to only steps created after the given turn boundary.
      if (sinceStepId !== undefined && step.id <= sinceStepId) continue;
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
 * Citation post-check pipeline, run check-on-use: walk the trace (scoped to
 * steps after `sinceStepId` when given) → collect allowed citations → strip
 * any citation tokens the agent invented. Turns with no in-scope
 * search_memory call skip the check entirely. Returns the sanitized answer
 * and the list of invalid citation ids.
 */
export function verifyAnswerCitations(
  trace: RunTrace | null,
  answer: string,
  sinceStepId?: number
): { answer: string; invalidCitations: string[] } {
  const bundles = collectBundlesFromTrace(trace, sinceStepId);
  // Check-on-use: only validate/strip citations on turns where
  // search_memory was actually called in-scope. No bundles -> nothing to
  // validate against, nothing to strip (leave the answer untouched).
  if (bundles.length === 0) {
    return { answer, invalidCitations: [] };
  }
  const allowed = collectAllowedCitations(bundles);
  const { sanitizedAnswer, invalid } = checkCitations(answer, allowed);
  return { answer: sanitizedAnswer, invalidCitations: invalid };
}
