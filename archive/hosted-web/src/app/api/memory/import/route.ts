import { NextResponse } from "next/server";
import { getDb } from "@/lib/db";
import { ingestFiles, type UploadFile } from "@/lib/memory/ingest";
import { requireActor } from "@/lib/auth/actor";

// AFK import: accept uploaded file(s) as multipart/form-data and turn them into
// source records via the shared ingest core. Per-file outcomes (incl. skips with
// reasons) come back in the IngestResult — one bad file never sinks the batch.
// TODO: upload size/count limits before this is exposed beyond single-user/local.
export async function POST(request: Request): Promise<Response> {
  let form: FormData;
  try {
    form = await request.formData();
  } catch {
    return NextResponse.json(
      { error: "expected multipart/form-data with file uploads" },
      { status: 400 }
    );
  }

  const uploads = form.getAll("files").filter((value): value is File => value instanceof File);
  if (uploads.length === 0) {
    return NextResponse.json({ error: "no files attached" }, { status: 400 });
  }

  const files: UploadFile[] = await Promise.all(
    uploads.map(async (file) => ({
      name: file.name,
      bytes: new Uint8Array(await file.arrayBuffer()),
    }))
  );

  try {
    // The importing director is granted read on every source this creates
    // (chunk-store issues the grant from this actor), so uploads land in their
    // memory rather than a shared pool.
    const result = await ingestFiles(getDb(), files, await requireActor());
    return NextResponse.json(result, { status: 200 });
  } catch (err) {
    const message = err instanceof Error ? err.message : "import failed";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
