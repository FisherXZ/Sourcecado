import { NextResponse } from "next/server";
import { getDb } from "@/lib/db";
import { listSources } from "@/lib/memory/sources";
import { requireActor } from "@/lib/auth/actor";

// Management list of the actor's sources (includes archived, flagged) for the
// /memory Sources tab. Permission-filtered (default-deny) inside listSources.
export async function GET(): Promise<Response> {
  try {
    const sources = await listSources(getDb(), await requireActor());
    return NextResponse.json({ sources });
  } catch (err) {
    const message = err instanceof Error ? err.message : "failed to list sources";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
