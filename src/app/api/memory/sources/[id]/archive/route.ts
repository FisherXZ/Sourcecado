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
  const body = await request.json().catch(() => null);
  const archived = (body as { archived?: unknown } | null)?.archived !== false;

  try {
    const result = await setSourceArchived(getDb(), { sourceId: id, archived });
    if (!result) {
      return NextResponse.json({ error: "source not found" }, { status: 404 });
    }
    return NextResponse.json(result, { status: 200 });
  } catch (err) {
    const message = err instanceof Error ? err.message : "archive failed";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
