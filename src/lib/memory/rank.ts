// Pure ranking and intent helpers — no DB calls, no side effects.
// Ported verbatim from src/answer.ts (legacy); prose/answer functions not included.

export type QuestionIntent =
  | "follow_up"
  | "responded"
  | "no_response"
  | "worked_with"
  | "uncertainty"
  | "generic";

// Structural shape required by factIntentScore and rankRows.
interface FactRow {
  subject: string;
  predicate: string;
  object: string;
}

// ---------------------------------------------------------------------------
// Exported pure functions
// ---------------------------------------------------------------------------

export function asksForUncertainty(question: string): boolean {
  return /\b(uncertain|gap|gaps|conflict|conflicted|candidate|stale|missing)\b/i.test(question);
}

export function questionIntent(question: string): QuestionIntent {
  if (asksForUncertainty(question)) {
    return "uncertainty";
  }
  if (/\b(worked with|work with|partnered|collaborated|sponsored|client project)\b/i.test(question)) {
    return "worked_with";
  }
  if (
    /\b(did not respond|didn't respond|no response|not respond|ghosted|did not reply|no reply)\b/i.test(
      question
    )
  ) {
    return "no_response";
  }
  if (/\b(responded|replied|reply|in discussion)\b/i.test(question)) {
    return "responded";
  }
  if (/\b(follow[- ]?up|needs follow|need follow)\b/i.test(question)) {
    return "follow_up";
  }
  return "generic";
}

export function meaningfulQuestionTerms(question: string): string[] {
  const terms = new Set(question.toLowerCase().match(/[a-z0-9]+/g) ?? []);
  const stopWords = new Set([
    "a",
    "an",
    "and",
    "for",
    "happened",
    "is",
    "needs",
    "the",
    "to",
    "what",
    "who",
    "with",
  ]);
  return Array.from(terms).filter((term) => term.length > 2 && !stopWords.has(term));
}

export function lexicalScore(text: string, question: string): number {
  const meaningfulTerms = meaningfulQuestionTerms(question);
  const normalizedText = text.toLowerCase();
  return meaningfulTerms.reduce(
    (score, term) => score + (normalizedText.includes(term) ? 1 : 0),
    0
  );
}

export function factIntentScore(fact: FactRow, intent: QuestionIntent): number {
  const predicate = fact.predicate.toLowerCase();
  const object = fact.object.toLowerCase();
  const text = `${predicate} ${object}`;

  if (intent === "follow_up") {
    return isFollowUpFact(fact) ? 2 : 0;
  }
  if (intent === "responded") {
    if (predicate === "status" && /\b(responded|replied|in discussion|locked in)\b/.test(object)) {
      return 3;
    }
    return /\b(responded|replied|locked in)\b/.test(text) ? 2 : 0;
  }
  if (intent === "no_response") {
    if (
      predicate === "status" &&
      /\b(ghosted|rejected|no response|no reply|declined)\b/.test(object)
    ) {
      return 3;
    }
    return /\b(ghosted|no response|no reply|not respond|did not respond|declined)\b/.test(text)
      ? 2
      : 0;
  }
  if (intent === "worked_with") {
    if (predicate === "codeology_owner" || predicate === "interest") {
      return 0;
    }
    if (predicate === "status" && /\b(locked in)\b/.test(object)) {
      return 3;
    }
    return /\b(worked with|partnered|collaborated|sponsored|sponsor|sow)\b/.test(text) ? 2 : 0;
  }
  return 0;
}

export function rankRows<T>(
  rows: T[],
  question: string,
  intent: QuestionIntent,
  getText: (row: T) => string
): T[] {
  const meaningfulTerms = meaningfulQuestionTerms(question);
  return rows
    .map((row, index) => {
      const score = lexicalScore(getText(row), question);
      const intentScore = isFactRow(row) ? factIntentScore(row, intent) : 0;
      return { row, score, intentScore, index };
    })
    .filter(({ score, intentScore }) =>
      intentRequiresSemanticMatch(intent)
        ? intentScore > 0
        : meaningfulTerms.length === 0 || score > 0 || intentScore > 0
    )
    .sort(
      (left, right) =>
        right.intentScore - left.intentScore ||
        right.score - left.score ||
        left.index - right.index
    )
    .map(({ row }) => row);
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

function isFollowUpFact(fact: FactRow): boolean {
  return (
    fact.predicate === "needs_follow_up" ||
    fact.predicate === "reason" ||
    fact.object.toLowerCase().includes("follow")
  );
}

function isFactRow(row: unknown): row is FactRow {
  return (
    typeof row === "object" &&
    row !== null &&
    "subject" in row &&
    "predicate" in row &&
    "object" in row
  );
}

function isStrictIntent(intent: QuestionIntent): boolean {
  return intent === "responded" || intent === "no_response" || intent === "worked_with";
}

function intentRequiresSemanticMatch(intent: QuestionIntent): boolean {
  return isStrictIntent(intent);
}
