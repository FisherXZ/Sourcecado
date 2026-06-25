import { NextResponse } from "next/server";
import { getDb } from "@/lib/db";
import { runAgent } from "@/lib/harness";
import { getRunTrace } from "@/lib/ledger";
import { verifyAnswerCitations } from "@/lib/memory/citations";
import { memoryRegistry, MEMORY_INSTRUCTIONS } from "@/lib/memory/answer-config";

export async function POST(request: Request) {
  const body = await request.json().catch(() => null);
  const question = (body as { question?: unknown } | null)?.question;
  if (typeof question !== "string" || !question.trim()) {
    return NextResponse.json({ error: "question is required" }, { status: 400 });
  }

  try {
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
      const checked = verifyAnswerCitations(trace, answer);
      answer = checked.answer;
      invalidCitations = checked.invalidCitations;
    }

    return NextResponse.json(
      { runId: result.runId, status: result.status, answer, steps: result.steps, invalidCitations },
      { status: result.status === "succeeded" ? 200 : 500 }
    );
  } catch (err) {
    const message = err instanceof Error ? err.message : "agent run failed";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
