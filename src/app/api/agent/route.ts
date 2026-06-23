import { NextResponse } from "next/server";
import { runAgent } from "@/lib/harness";
import { echoTool } from "@/lib/tools/echo";
import { createToolRegistry } from "@/lib/tools/registry";

export async function POST(request: Request) {
  const body = await request.json().catch(() => null);
  const question = (body as { question?: unknown } | null)?.question;
  if (typeof question !== "string" || !question.trim()) {
    return NextResponse.json({ error: "question is required" }, { status: 400 });
  }

  const registry = createToolRegistry([echoTool]);
  const result = await runAgent({ question, registry });
  return NextResponse.json(result, { status: result.status === "succeeded" ? 200 : 500 });
}
