import { NextResponse } from "next/server";
import { getDb } from "@/lib/db";
import { addMemoryNote } from "@/lib/memory/notes";

// Add a memory note (or a correction = a superseding note). Dedicated write path
// — NOT the read-only /api/agent registry. Becomes immediately retrievable.
export async function POST(request: Request): Promise<Response> {
  const body = await request.json().catch(() => null);
  const title = (body as { title?: unknown } | null)?.title;
  const text = (body as { text?: unknown } | null)?.text;
  if (typeof title !== "string" || !title.trim() || typeof text !== "string" || !text.trim()) {
    return NextResponse.json({ error: "title and text are required" }, { status: 400 });
  }

  try {
    const result = await addMemoryNote(getDb(), { title, text });
    return NextResponse.json(result, { status: 200 });
  } catch (err) {
    // Log the real failure server-side; return a stable message so internal
    // DB/embedding details never reach the browser.
    console.error("failed to add note", err);
    return NextResponse.json({ error: "failed to add note" }, { status: 500 });
  }
}
