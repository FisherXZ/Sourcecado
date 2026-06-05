import type { MemoryDatabase } from "./db.js";
import { cosineSimilarity, deserializeEmbedding, embedText } from "./embeddings.js";

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

export function buildSourcingMemoryAnswer(db: MemoryDatabase, question: string): string {
  const sourceCount = getSourceRecordCount(db);
  const questionLine = question.trim() ? ` for "${question.trim()}"` : "";

  if (sourceCount === 0) {
    return noMemoryAnswer(questionLine);
  }

  const acceptedFacts = loadFacts(db, "accepted", question);
  const gapFacts = loadGapFacts(db, question);
  const chunks = retrieveRelevantChunks(db, question);

  if (acceptedFacts.length === 0 && chunks.length === 0) {
    return noRelevantMemoryAnswer(questionLine, gapFacts);
  }

  const answerFacts = selectAnswerFacts(acceptedFacts, question);

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
    ...nextActionLines(answerFacts, gapFacts, question)
  ].join("\n");
}

export const buildNoMemoryAnswer = buildSourcingMemoryAnswer;

function getSourceRecordCount(db: MemoryDatabase): number {
  const row = db.prepare("select count(*) as count from source_records").get() as { count: number };
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
    ...nextActionLines([], gapFacts, questionLine)
  ].join("\n");
}

function loadFacts(db: MemoryDatabase, status: string, question: string): FactRow[] {
  const rows = db
    .prepare(
      [
        "select semantic_facts.subject, semantic_facts.predicate, semantic_facts.object,",
        "semantic_facts.confidence, semantic_facts.status, memory_chunks.citation",
        "from semantic_facts",
        "left join memory_chunks on memory_chunks.id = semantic_facts.source_chunk_id",
        "where semantic_facts.status = ?",
        "order by semantic_facts.confidence desc, semantic_facts.subject"
      ].join(" ")
    )
    .all(status) as FactRow[];

  const ranked = rankRows(rows, question, (row) => `${row.subject} ${row.predicate} ${row.object}`);
  return ranked.slice(0, 6);
}

function loadGapFacts(db: MemoryDatabase, question: string): FactRow[] {
  const rows = db
    .prepare(
      [
        "select semantic_facts.subject, semantic_facts.predicate, semantic_facts.object,",
        "semantic_facts.confidence, semantic_facts.status, memory_chunks.citation",
        "from semantic_facts",
        "left join memory_chunks on memory_chunks.id = semantic_facts.source_chunk_id",
        "where semantic_facts.status in ('candidate', 'conflicted', 'stale')",
        "order by semantic_facts.status, semantic_facts.subject"
      ].join(" ")
    )
    .all() as FactRow[];

  if (asksForUncertainty(question)) {
    return rows.slice(0, 6);
  }

  return rankRows(rows, question, (row) => `${row.subject} ${row.predicate} ${row.object}`).slice(0, 6);
}

function retrieveRelevantChunks(db: MemoryDatabase, question: string): ChunkRow[] {
  const chunks = db
    .prepare("select text, citation, embedding from memory_chunks order by id")
    .all() as ChunkRow[];
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

function rankRows<T>(rows: T[], question: string, getText: (row: T) => string): T[] {
  const meaningfulTerms = meaningfulQuestionTerms(question);
  return rows
    .map((row, index) => {
      const score = lexicalScore(getText(row), question);
      return { row, score, index };
    })
    .filter(({ score }) => meaningfulTerms.length === 0 || score > 0)
    .sort((left, right) => right.score - left.score || left.index - right.index)
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

function selectAnswerFacts(facts: FactRow[], question: string): FactRow[] {
  const needsFollowUp = facts.filter(
    (fact) => isFollowUpFact(fact) && subjectMatchesQuestionTopic(fact.subject, facts, question)
  );
  return needsFollowUp.length > 0 ? needsFollowUp : facts;
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

function nextActionLines(facts: FactRow[], gaps: FactRow[], question: string): string[] {
  const followUpFact = facts.find(
    (fact) =>
      fact.predicate === "needs_follow_up" ||
      fact.predicate === "reason" ||
      fact.object.toLowerCase().includes("follow")
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
