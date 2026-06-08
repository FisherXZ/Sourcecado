import type { MemoryDatabase } from "./db.js";
import { cosineSimilarity, deserializeEmbedding, embedText } from "./embeddings.js";
import { sourceIdInClause, type SourceScope } from "./read-service.js";

interface FactRow {
  subject: string;
  predicate: string;
  object: string;
  confidence: number;
  status: string;
  citation: string | null;
}

interface ChunkRow {
  text: string;
  citation: string;
  embedding: string | null;
}

type QuestionIntent = "follow_up" | "responded" | "no_response" | "worked_with" | "uncertainty" | "generic";

export function buildSourcingMemoryAnswer(
  db: MemoryDatabase,
  question: string,
  scope: SourceScope
): string {
  const sourceCount = getSourceRecordCount(db, scope);
  const questionLine = question.trim() ? ` for "${question.trim()}"` : "";
  const intent = questionIntent(question);

  if (sourceCount === 0) {
    return noMemoryAnswer(questionLine);
  }

  const acceptedFacts = loadFacts(db, "accepted", question, intent, scope);
  const gapFacts = loadGapFacts(db, question, scope);
  const chunks = retrieveRelevantChunks(db, question, scope);

  if (acceptedFacts.length === 0 && chunks.length === 0) {
    return noRelevantMemoryAnswer(questionLine, gapFacts);
  }

  const answerFacts = selectAnswerFacts(acceptedFacts, question, intent);
  if (isStrictIntent(intent) && answerFacts.length === 0) {
    return noRelevantMemoryAnswer(questionLine, gapFacts);
  }

  return [
    "Answer:",
    ...answerLines(answerFacts, chunks, question),
    "",
    "Evidence:",
    ...evidenceLines(answerFacts, chunks),
    "",
    "Gaps:",
    ...gapLines(gapFacts),
    "",
    "Next Action:",
    ...nextActionLines(answerFacts, gapFacts, question, intent)
  ].join("\n");
}

export const buildNoMemoryAnswer = buildSourcingMemoryAnswer;

function getSourceRecordCount(db: MemoryDatabase, scope: SourceScope): number {
  if (scope.allowedSourceIds.length === 0) {
    return 0;
  }
  const { sql, params } = sourceIdInClause(scope, "source_records");
  const row = db
    .prepare(`select count(*) as count from source_records where ${sql}`)
    .get(...params) as { count: number };
  return row.count;
}

function noMemoryAnswer(questionLine: string): string {
  return [
    "Answer:",
    `I do not have any indexed sourcing memory yet${questionLine}, so I cannot identify who needs follow-up from sources.`,
    "",
    "Evidence:",
    "- No sources found.",
    "",
    "Gaps:",
    "- The local memory database is empty.",
    "- No source records, chunks, entities, relationships, or semantic facts have been indexed yet.",
    "",
    "Next Action:",
    "- Run `sourcyavo ingest seed-data/`, then `sourcyavo refresh`, and ask again."
  ].join("\n");
}

function noRelevantMemoryAnswer(questionLine: string, gapFacts: FactRow[]): string {
  return [
    "Answer:",
    `I do not have accepted sourcing facts${questionLine} yet.`,
    "",
    "Evidence:",
    "- No accepted cited facts found.",
    "",
    "Gaps:",
    ...gapLines(gapFacts),
    "",
    "Next Action:",
    ...nextActionLines([], gapFacts, questionLine, "generic")
  ].join("\n");
}

function loadFacts(
  db: MemoryDatabase,
  status: string,
  question: string,
  intent: QuestionIntent,
  scope: SourceScope
): FactRow[] {
  if (scope.allowedSourceIds.length === 0) {
    return [];
  }
  const { sql: scopeSql, params: scopeParams } = sourceIdInClause(scope, "sr");
  const rows = db
    .prepare(
      [
        "select semantic_facts.subject, semantic_facts.predicate, semantic_facts.object,",
        "semantic_facts.confidence, semantic_facts.status, memory_chunks.citation",
        "from semantic_facts",
        "join source_records sr on sr.id = semantic_facts.source_record_id",
        "left join memory_chunks on memory_chunks.id = semantic_facts.source_chunk_id",
        `where semantic_facts.status = ? and ${scopeSql}`,
        "order by semantic_facts.confidence desc, semantic_facts.subject"
      ].join(" ")
    )
    .all(status, ...scopeParams) as FactRow[];

  const ranked = rankRows(rows, question, intent, (row) => `${row.subject} ${row.predicate} ${row.object}`);
  return ranked.slice(0, 6);
}

function loadGapFacts(db: MemoryDatabase, question: string, scope: SourceScope): FactRow[] {
  if (scope.allowedSourceIds.length === 0) {
    return [];
  }
  const { sql: scopeSql, params: scopeParams } = sourceIdInClause(scope, "sr");
  const rows = db
    .prepare(
      [
        "select semantic_facts.subject, semantic_facts.predicate, semantic_facts.object,",
        "semantic_facts.confidence, semantic_facts.status, memory_chunks.citation",
        "from semantic_facts",
        "join source_records sr on sr.id = semantic_facts.source_record_id",
        "left join memory_chunks on memory_chunks.id = semantic_facts.source_chunk_id",
        `where semantic_facts.status in ('candidate', 'conflicted', 'stale') and ${scopeSql}`,
        "order by semantic_facts.status, semantic_facts.subject"
      ].join(" ")
    )
    .all(...scopeParams) as FactRow[];

  if (asksForUncertainty(question)) {
    return rows.slice(0, 6);
  }

  return rankRows(rows, question, "generic", (row) => `${row.subject} ${row.predicate} ${row.object}`).slice(0, 6);
}

function retrieveRelevantChunks(db: MemoryDatabase, question: string, scope: SourceScope): ChunkRow[] {
  if (scope.allowedSourceIds.length === 0) {
    return [];
  }
  const { sql: scopeSql, params: scopeParams } = sourceIdInClause(scope, "sr");
  const chunks = db
    .prepare(
      [
        "select memory_chunks.text, memory_chunks.citation, memory_chunks.embedding",
        "from memory_chunks",
        "join source_records sr on sr.id = memory_chunks.source_record_id",
        `where ${scopeSql}`,
        "order by memory_chunks.id"
      ].join(" ")
    )
    .all(...scopeParams) as ChunkRow[];
  const queryEmbedding = embedText(question);

  return chunks
    .map((chunk) => ({
      chunk,
      score: cosineSimilarity(queryEmbedding, deserializeEmbedding(chunk.embedding))
    }))
    .filter(({ chunk, score }) => score > 0 && lexicalScore(chunk.text, question) > 0)
    .sort((left, right) => right.score - left.score)
    .slice(0, 3)
    .map(({ chunk }) => chunk);
}

function rankRows<T>(
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
        right.intentScore - left.intentScore || right.score - left.score || left.index - right.index
    )
    .map(({ row }) => row);
}

function lexicalScore(text: string, question: string): number {
  const meaningfulTerms = meaningfulQuestionTerms(question);
  const normalizedText = text.toLowerCase();
  return meaningfulTerms.reduce(
    (score, term) => score + (normalizedText.includes(term) ? 1 : 0),
    0
  );
}

function meaningfulQuestionTerms(question: string): string[] {
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
    "with"
  ]);
  return Array.from(terms).filter((term) => term.length > 2 && !stopWords.has(term));
}

function topicQuestionTerms(question: string): string[] {
  const actionTerms = new Set(["follow", "followup", "need", "needs"]);
  return meaningfulQuestionTerms(question).filter((term) => !actionTerms.has(term));
}

function asksForUncertainty(question: string): boolean {
  return /\b(uncertain|gap|gaps|conflict|conflicted|candidate|stale|missing)\b/i.test(question);
}

function questionIntent(question: string): QuestionIntent {
  if (asksForUncertainty(question)) {
    return "uncertainty";
  }
  if (/\b(worked with|work with|partnered|collaborated|sponsored|client project)\b/i.test(question)) {
    return "worked_with";
  }
  if (/\b(did not respond|didn't respond|no response|not respond|ghosted|did not reply|no reply)\b/i.test(question)) {
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

function answerLines(facts: FactRow[], chunks: ChunkRow[], question: string): string[] {
  if (facts.length === 0 && chunks.length === 0) {
    return [`I do not have accepted sourcing facts for "${question.trim()}" yet.`];
  }

  if (facts.length === 0) {
    return [
      `- I found relevant source chunks for "${question.trim()}", but no accepted structured facts yet.`,
      `- Most relevant memory: ${truncate(chunks[0]?.text ?? "", 180)}`
    ];
  }

  return facts.map((fact) => {
    const predicate = fact.predicate.replace(/_/g, " ");
    const followUpText =
      fact.predicate === "needs_follow_up"
        ? `needs follow-up: ${fact.object}`
        : fact.predicate === "reason"
          ? "needs follow-up"
          : `${predicate} is ${fact.object}`;
    return `- ${fact.subject} ${followUpText}.`;
  });
}

function selectAnswerFacts(
  facts: FactRow[],
  question: string,
  intent: QuestionIntent
): FactRow[] {
  if (intent === "responded") {
    return facts.filter((fact) => factIntentScore(fact, "responded") > 0);
  }
  if (intent === "no_response") {
    return facts.filter((fact) => factIntentScore(fact, "no_response") > 0);
  }
  if (intent === "worked_with") {
    return facts.filter((fact) => factIntentScore(fact, "worked_with") > 0);
  }

  const needsFollowUp = facts.filter(
    (fact) => isFollowUpFact(fact) && subjectMatchesQuestionTopic(fact.subject, facts, question)
  );
  return needsFollowUp.length > 0 ? needsFollowUp : facts;
}

function isStrictIntent(intent: QuestionIntent): boolean {
  return intent === "responded" || intent === "no_response" || intent === "worked_with";
}

function intentRequiresSemanticMatch(intent: QuestionIntent): boolean {
  return isStrictIntent(intent);
}

function factIntentScore(fact: FactRow, intent: QuestionIntent): number {
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
    if (predicate === "status" && /\b(ghosted|rejected|no response|no reply|declined)\b/.test(object)) {
      return 3;
    }
    return /\b(ghosted|no response|no reply|not respond|did not respond|declined)\b/.test(text) ? 2 : 0;
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

function isFactRow(row: unknown): row is FactRow {
  return (
    typeof row === "object" &&
    row !== null &&
    "subject" in row &&
    "predicate" in row &&
    "object" in row
  );
}

function isFollowUpFact(fact: FactRow): boolean {
  return (
    fact.predicate === "needs_follow_up" ||
    fact.predicate === "reason" ||
    fact.object.toLowerCase().includes("follow")
  );
}

function subjectMatchesQuestionTopic(subject: string, facts: FactRow[], question: string): boolean {
  const topicTerms = topicQuestionTerms(question);
  if (topicTerms.length === 0) {
    return true;
  }

  return facts.some((fact) => {
    if (fact.subject !== subject) {
      return false;
    }
    const text = `${fact.subject} ${fact.predicate} ${fact.object}`.toLowerCase();
    return topicTerms.some((term) => text.includes(term));
  });
}

function evidenceLines(facts: FactRow[], chunks: ChunkRow[]): string[] {
  const citations = new Set<string>();
  for (const fact of facts) {
    if (fact.citation) {
      citations.add(fact.citation);
    }
  }
  if (citations.size === 0) {
    for (const chunk of chunks) {
      citations.add(chunk.citation);
    }
  }

  if (citations.size === 0) {
    return ["- No source citations found."];
  }

  return Array.from(citations)
    .slice(0, 6)
    .map((citation) => `- ${citation}`);
}

function gapLines(facts: FactRow[]): string[] {
  if (facts.length === 0) {
    return ["- No candidate, conflicted, or stale facts surfaced for this question."];
  }

  return facts.map(
    (fact) =>
      `- ${fact.subject} has ${fact.status} memory: ${fact.predicate} = ${fact.object}${
        fact.citation ? ` (${fact.citation})` : ""
      }.`
  );
}

function nextActionLines(
  facts: FactRow[],
  gaps: FactRow[],
  question: string,
  intent: QuestionIntent
): string[] {
  const followUpFact = facts.find(
    (fact) =>
      intent !== "no_response" &&
      (fact.predicate === "needs_follow_up" ||
        fact.predicate === "reason" ||
        fact.object.toLowerCase().includes("follow"))
  );
  if (followUpFact) {
    return [`- Follow up with ${followUpFact.subject} and verify the latest outreach state.`];
  }
  if (gaps.length > 0) {
    return ["- Resolve the surfaced gaps before treating this answer as final."];
  }
  return [`- Review the cited sources and update memory if "${question.trim()}" needs more context.`];
}

function truncate(text: string, maxLength: number): string {
  if (text.length <= maxLength) {
    return text;
  }
  return `${text.slice(0, maxLength - 3)}...`;
}
