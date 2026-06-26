import { NextResponse } from "next/server";
import { getDb } from "@/lib/db";
import { setSourceArchived } from "@/lib/memory/sources";

// Soft-archive (default) or un-archive a source. Body { archived?: boolean } —
// omit or true to archive, false to restore. 404 if the source is unknown or
// the actor isn't permitted (setSourceArchived returns null, default-deny).
export async function POST(
  request: Request,
  { params }: { params: Promise<{ id: string }> }
): Promise<Response> {
  const { id } = await params;

  // Default to archive only when the body is genuinely absent/empty. Malformed
  // JSON is a client error, not an implicit "archive" — reject it with 400.
  const raw = await request.text();
  let archived = true;
  if (raw.trim()) {
    let body: unknown;
    try {
      body = JSON.parse(raw);
    } catch {
      return NextResponse.json({ error: "invalid request body" }, { status: 400 });
    }
    if (body === null || typeof body !== "object") {
      return NextResponse.json({ error: "invalid request body" }, { status: 400 });
    }
    archived = (body as { archived?: unknown }).archived !== false;
  }

  try {
    const result = await setSourceArchived(getDb(), { sourceId: id, archived });
    if (!result) {
      return NextResponse.json({ error: "source not found" }, { status: 404 });
    }
    return NextResponse.json(result, { status: 200 });
  } catch (err) {
    // Log the real failure server-side; return a stable message (same rule as
    // the note route — internal DB details must not reach the browser).
    console.error("archive failed", err);
    return NextResponse.json({ error: "archive failed" }, { status: 500 });
  }
}
