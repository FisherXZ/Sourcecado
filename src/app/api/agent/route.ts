import { NextResponse } from "next/server";
import { getDb } from "@/lib/db";
import { runAgent } from "@/lib/harness";
import { getRunTrace, type RunTrace, type RunStepTrace } from "@/lib/ledger";
import { collectAllowedCitations, checkCitations } from "@/lib/memory/citations";
import { memoryRegistry, MEMORY_INSTRUCTIONS } from "@/lib/memory/answer-config";
import type { MemoryBundle } from "@/lib/memory/retrieve";

function collectBundlesFromTrace(trace: RunTrace): MemoryBundle[] {
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

export async function POST(request: Request) {
  const body = await request.json().catch(() => null);
  const question = (body as { question?: unknown } | null)?.question;
  if (typeof question !== "string" || !question.trim()) {
    return NextResponse.json({ error: "question is required" }, { status: 400 });
  }

  const db = getDb();
  const registry = memoryRegistry();
  const result = await runAgent({
    question,
    registry,
    allowedClasses: new Set(["read"]),
    instructions: MEMORY_INSTRUCTIONS,
    db,
  });

  let answer = result.answer;
  let invalidCitations: string[] = [];

  if (result.status === "succeeded" && answer !== undefined) {
    const trace = await getRunTrace(db, result.runId);
    if (trace) {
      const bundles = collectBundlesFromTrace(trace);
      const allowed = collectAllowedCitations(bundles);
      const checked = checkCitations(answer, allowed);
      answer = checked.sanitizedAnswer;
      invalidCitations = checked.invalid;
    }
  }

  return NextResponse.json(
    { runId: result.runId, status: result.status, answer, steps: result.steps, invalidCitations },
    { status: result.status === "succeeded" ? 200 : 500 }
  );
}
