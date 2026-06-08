import { cpSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createDatabase, type MemoryDatabase } from "../../src/db.js";
import { ingestFolder } from "../../src/ingest.js";
import { refreshMemory, type RefreshMemoryOptions } from "../../src/refresh.js";
import { MemoryReader, resolveAccessContext } from "../../src/read-service.js";
import {
  BENCHMARK_CASES,
  STRESS_ALLOWED_SOURCE_IDS,
  STRESS_CLIENT,
  type BenchmarkCase
} from "../fixtures/stress/benchmark.js";

// Six fixed miss categories evaluated first-match-wins in this order. 'temporal'
// is structurally out of scope (Checkpoint 4) and is a declared deferred bucket
// (always count 0) rather than a fabricated pass.
export const MISS_CATEGORIES = [
  "permission",
  "missing-source",
  "extraction",
  "retrieval",
  "citation",
  "temporal"
] as const;
export type MissCategory = (typeof MISS_CATEGORIES)[number];

export type CaseOutcome = "pass" | "miss";

// When S2 permissions cannot be evaluated, the permission bucket degrades to
// this honest sentinel instead of silently passing.
export const PERMISSION_UNREACHABLE = "unreachable-pending" as const;
export type PermissionState = "reachable" | typeof PERMISSION_UNREACHABLE;

export interface CaseResult {
  id: string;
  question: string;
  client: { actorType: string; actorId: string };
  outcome: CaseOutcome;
  category: MissCategory | null;
  detail: string;
}

export interface StressReport {
  snapshotPath: string;
  ingest: {
    processed: number;
    skipped: number;
  };
  // Relative-ish labels of files that ingest skipped (from ingest_errors).
  skippedFiles: Array<{ path: string; category: string; reason: string }>;
  // Refresh/extraction failures (extraction_runs with status='failed').
  extractionFailures: Array<{ chunkId: number | null; error: string }>;
  refresh: {
    chunksProcessed: number;
    extracted: number;
    reused: number;
    failed: number;
  };
  cases: CaseResult[];
  // Six-category tally. 'temporal' is always present with count 0.
  tally: Record<MissCategory, number>;
  permissionState: PermissionState;
  passCount: number;
  missCount: number;
}

export interface StressHarnessOptions {
  // Source folder to ingest. Defaults to the committed stress corpus. Tests
  // point this at a seeded-failure corpus to drive specific categories.
  corpusPath?: string;
  // Override the cases to run. Defaults to the committed benchmark set.
  cases?: readonly BenchmarkCase[];
  // Source ids the stress client is granted read access to. Defaults to the
  // committed non-restricted allowlist. An empty array models a permission gap
  // (client present, zero grants). S2 being unavailable is modeled separately
  // by simulateMissingPermissions.
  allowedSourceIds?: readonly string[];
  // Forwarded to refreshMemory so a test can inject a deterministic extractor
  // (e.g. one that yields nothing) to seed an extraction miss. No LLM is ever
  // called by the default path.
  refreshOptions?: RefreshMemoryOptions;
  // When true, do not write any source_permissions rows and report the
  // permission bucket as 'unreachable-pending'. Models S2 being absent.
  simulateMissingPermissions?: boolean;
}

interface FactRow {
  subject: string;
  predicate: string;
  object: string;
  status: string;
  citation: string | null;
  source_id: string;
}

const DEFAULT_CORPUS = new URL("../fixtures/stress/corpus/", import.meta.url).pathname;

export async function runStressHarness(
  options: StressHarnessOptions = {}
): Promise<StressReport> {
  const corpusPath = options.corpusPath ?? DEFAULT_CORPUS;
  const cases = options.cases ?? BENCHMARK_CASES;
  const tempRoot = mkdtempSync(join(tmpdir(), "sourcyavo-stress-"));

  try {
    const corpusCopy = join(tempRoot, "corpus");
    cpSync(corpusPath, corpusCopy, { recursive: true });

    const snapshotPath = join(tempRoot, ".sourcyavo", "memory.db");
    const db = createDatabase(snapshotPath);

    try {
      return await buildReport(db, corpusCopy, snapshotPath, cases, options);
    } finally {
      db.close();
    }
  } finally {
    rmSync(tempRoot, { recursive: true, force: true });
  }
}

async function buildReport(
  db: MemoryDatabase,
  corpusCopy: string,
  snapshotPath: string,
  cases: readonly BenchmarkCase[],
  options: StressHarnessOptions
): Promise<StressReport> {
  const ingestResult = ingestFolder(db, corpusCopy);
  const refresh = await refreshMemory(db, options.refreshOptions);

  const permissionState: PermissionState = options.simulateMissingPermissions
    ? PERMISSION_UNREACHABLE
    : "reachable";

  if (permissionState === "reachable") {
    seedPermissions(db, options.allowedSourceIds ?? STRESS_ALLOWED_SOURCE_IDS);
  }

  const skippedFiles = loadSkippedFiles(db);
  const extractionFailures = loadExtractionFailures(db);

  const tally = emptyTally();
  const caseResults: CaseResult[] = [];

  for (const benchmark of cases) {
    const result = evaluateCase(db, benchmark, permissionState);
    caseResults.push(result);
    if (result.category) {
      tally[result.category] += 1;
    }
  }

  const passCount = caseResults.filter((c) => c.outcome === "pass").length;
  const missCount = caseResults.length - passCount;

  return {
    snapshotPath,
    ingest: { processed: ingestResult.processed, skipped: ingestResult.skipped },
    skippedFiles: skippedFiles.map((row) => ({
      path: row.path,
      category: row.category,
      reason: row.reason
    })),
    extractionFailures,
    refresh,
    cases: caseResults,
    tally,
    permissionState,
    passCount,
    missCount
  };
}

function seedPermissions(db: MemoryDatabase, sourceIds: readonly string[]): void {
  const insert = db.prepare(
    "insert or ignore into source_permissions (principal_type, principal_id, source_id, access) values (?, ?, ?, 'read')"
  );
  for (const sourceId of sourceIds) {
    insert.run(STRESS_CLIENT.actorType, STRESS_CLIENT.actorId, sourceId);
  }
}

function evaluateCase(
  db: MemoryDatabase,
  benchmark: BenchmarkCase,
  permissionState: PermissionState
): CaseResult {
  const ctx = resolveAccessContext(db, {
    actorType: benchmark.client.actorType,
    actorId: benchmark.client.actorId
  });
  const reader = new MemoryReader(db, ctx);
  const answer = reader.ask(benchmark.question);
  const evidence = extractEvidenceSection(answer);

  const base = {
    id: benchmark.id,
    question: benchmark.question,
    client: { actorType: benchmark.client.actorType, actorId: benchmark.client.actorId }
  };

  const category = classifyOutcome(db, benchmark, ctx.allowedSourceIds, answer, evidence, permissionState);
  if (!category) {
    return { ...base, outcome: "pass", category: null, detail: "all expectations met" };
  }
  return { ...base, outcome: "miss", category: category.category, detail: category.detail };
}

// First-match-wins classification in the fixed order:
// permission -> missing-source -> extraction -> retrieval -> citation -> temporal.
function classifyOutcome(
  db: MemoryDatabase,
  benchmark: BenchmarkCase,
  allowedSourceIds: string[],
  answer: string,
  evidence: string,
  permissionState: PermissionState
): { category: MissCategory; detail: string } | null {
  const { expectations } = benchmark;
  const answerLower = answer.toLowerCase();

  // 1. permission: a forbidden subject or restricted citation leaked.
  if (permissionState === "reachable") {
    const leaked = expectations.forbiddenSubjects.find((subject) =>
      answerLower.includes(subject.toLowerCase())
    );
    if (leaked) {
      return {
        category: "permission",
        detail: `forbidden subject leaked into answer: ${leaked}`
      };
    }
  }
  // When permissions are unreachable we do not run the permission check; the
  // bucket is declared via permissionState and never silently passes.

  // For cases that legitimately expect no answer (e.g. a correctly scoped
  // client asking about a restricted topic), the only failure mode is a leak,
  // which was checked above. No leak => pass.
  if (!expectations.expectSomeAnswer) {
    return null;
  }

  // 2. missing-source: an expected cited source is absent from source_records
  // or present in ingest_errors.
  for (const expectedCitation of expectations.mustCiteSources) {
    const sourceRow = findSourceByCitationFragment(db, expectedCitation);
    if (!sourceRow) {
      return {
        category: "missing-source",
        detail: `expected source absent from source_records: ${expectedCitation}`
      };
    }
    if (sourceWasSkipped(db, expectedCitation)) {
      return {
        category: "missing-source",
        detail: `expected source recorded in ingest_errors: ${expectedCitation}`
      };
    }
  }

  // Load scoped accepted facts once for the remaining structural checks.
  const scopedFacts = loadScopedFacts(db, allowedSourceIds);

  for (const subject of expectations.mustMentionSubjects) {
    const subjectInAnswer = answerLower.includes(subject.toLowerCase());
    const acceptedForSubject = scopedFacts.filter(
      (fact) => fact.status === "accepted" && fact.subject.toLowerCase() === subject.toLowerCase()
    );

    // 3. extraction: a chunk exists for the subject's source but no semantic
    // fact was produced for that subject at all.
    if (acceptedForSubject.length === 0) {
      const anyFactForSubject = scopedFacts.some(
        (fact) => fact.subject.toLowerCase() === subject.toLowerCase()
      );
      if (!anyFactForSubject && chunkExistsForSubject(db, allowedSourceIds, subject)) {
        return {
          category: "extraction",
          detail: `chunk present but no semantic_facts for subject: ${subject}`
        };
      }
    }

    // 4. retrieval: an accepted fact exists in scope for the subject but the
    // subject never made it into the Answer.
    if (acceptedForSubject.length > 0 && !subjectInAnswer) {
      return {
        category: "retrieval",
        detail: `accepted fact present but subject missing from answer: ${subject}`
      };
    }
  }

  // 5. citation: a required subject is in the Answer but its expected citation
  // is missing from the Evidence section.
  const evidenceLower = evidence.toLowerCase();
  if (expectations.mustMentionSubjects.length > 0) {
    for (const expectedCitation of expectations.mustCiteSources) {
      if (!evidenceLower.includes(expectedCitation.toLowerCase())) {
        return {
          category: "citation",
          detail: `expected citation missing from evidence: ${expectedCitation}`
        };
      }
    }
  }

  // Any remaining unmet mustMention (e.g. subject absent and no accepted fact to
  // explain it) is reported as a retrieval miss so it is never silently passed.
  for (const subject of expectations.mustMentionSubjects) {
    if (!answerLower.includes(subject.toLowerCase())) {
      return {
        category: "retrieval",
        detail: `expected subject not surfaced in answer: ${subject}`
      };
    }
  }

  // 6. temporal: structurally out of scope (Checkpoint 4). Never reached; the
  // bucket exists in the tally with count 0 by construction.

  return null;
}

function loadScopedFacts(db: MemoryDatabase, allowedSourceIds: string[]): FactRow[] {
  if (allowedSourceIds.length === 0) {
    return [];
  }
  const placeholders = allowedSourceIds.map(() => "?").join(", ");
  return db
    .prepare(
      [
        "select semantic_facts.subject as subject, semantic_facts.predicate as predicate,",
        "semantic_facts.object as object, semantic_facts.status as status,",
        "memory_chunks.citation as citation, sr.source_id as source_id",
        "from semantic_facts",
        "join source_records sr on sr.id = semantic_facts.source_record_id",
        "left join memory_chunks on memory_chunks.id = semantic_facts.source_chunk_id",
        `where sr.source_id in (${placeholders})`
      ].join(" ")
    )
    .all(...allowedSourceIds) as FactRow[];
}

function chunkExistsForSubject(
  db: MemoryDatabase,
  allowedSourceIds: string[],
  subject: string
): boolean {
  if (allowedSourceIds.length === 0) {
    return false;
  }
  const placeholders = allowedSourceIds.map(() => "?").join(", ");
  const row = db
    .prepare(
      [
        "select count(*) as count",
        "from memory_chunks",
        "join source_records sr on sr.id = memory_chunks.source_record_id",
        `where sr.source_id in (${placeholders})`,
        "and lower(memory_chunks.text) like ?"
      ].join(" ")
    )
    .get(...allowedSourceIds, `%${subject.toLowerCase()}%`) as { count: number };
  return row.count > 0;
}

function findSourceByCitationFragment(
  db: MemoryDatabase,
  fragment: string
): { source_id: string } | undefined {
  return db
    .prepare("select source_id from source_records where path like ? limit 1")
    .get(`%${fragment}%`) as { source_id: string } | undefined;
}

function sourceWasSkipped(db: MemoryDatabase, fragment: string): boolean {
  const row = db
    .prepare("select count(*) as count from ingest_errors where path like ?")
    .get(`%${fragment}%`) as { count: number };
  return row.count > 0;
}

function loadSkippedFiles(
  db: MemoryDatabase
): Array<{ path: string; category: string; reason: string }> {
  return db
    .prepare(
      "select path, coalesce(category, 'internal-error') as category, reason from ingest_errors order by path"
    )
    .all() as Array<{ path: string; category: string; reason: string }>;
}

function loadExtractionFailures(
  db: MemoryDatabase
): Array<{ chunkId: number | null; error: string }> {
  return db
    .prepare(
      "select source_chunk_id as chunkId, coalesce(error, '') as error from extraction_runs where status = 'failed' order by id"
    )
    .all() as Array<{ chunkId: number | null; error: string }>;
}

function extractEvidenceSection(answer: string): string {
  const lines = answer.split("\n");
  const start = lines.findIndex((line) => line.trim() === "Evidence:");
  if (start === -1) {
    return "";
  }
  const rest = lines.slice(start + 1);
  const end = rest.findIndex((line) => /^[A-Z][A-Za-z ]+:$/.test(line.trim()));
  const sectionLines = end === -1 ? rest : rest.slice(0, end);
  return sectionLines.join("\n");
}

function emptyTally(): Record<MissCategory, number> {
  return {
    permission: 0,
    "missing-source": 0,
    extraction: 0,
    retrieval: 0,
    citation: 0,
    temporal: 0
  };
}
