import { useCallback, useEffect, useState } from "react";

import { getAttachment, type AttachmentInfo } from "../api";

/** "148 KB", "2.5 MB". The same words the backend uses in its own messages. */
export function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export const KIND_LABEL: Readonly<Record<AttachmentInfo["kind"], string>> = {
  pdf: "PDF",
  image: "Image",
  text: "Text",
  markdown: "Markdown",
  csv: "CSV",
};

/** "PDF · 3 pages · 148 KB". */
export function describeAttachment(info: AttachmentInfo): string {
  return [
    KIND_LABEL[info.kind],
    info.pages !== null ? `${info.pages} ${info.pages === 1 ? "page" : "pages"}` : null,
    formatBytes(info.size),
  ]
    .filter(Boolean)
    .join(" · ");
}

// A file's record is read once per id and kept. A sent message shows its chip
// from this cache, so the name is the one the backend kept, not text from the
// message. Null means the file is no longer on this computer.
const records = new Map<string, Promise<AttachmentInfo | null>>();

export function readAttachment(id: string): Promise<AttachmentInfo | null> {
  let record = records.get(id);
  if (!record) {
    record = getAttachment(id).catch((error: unknown) => {
      // A failed read is not an answer. Ask again next time.
      records.delete(id);
      throw error;
    });
    records.set(id, record);
  }
  return record;
}

/** A file just uploaded is known already. Its chip does not read it again. */
export function rememberAttachment(info: AttachmentInfo): void {
  records.set(info.id, Promise.resolve(info));
}

export function resetAttachments(): void {
  records.clear();
}

export type AttachmentRecord =
  | { readonly status: "loading" }
  | { readonly status: "ready"; readonly info: AttachmentInfo }
  | { readonly status: "unavailable" }
  | { readonly status: "failed" };

export function useAttachmentRecord(id: string): {
  readonly record: AttachmentRecord;
  readonly retry: () => void;
} {
  const [record, setRecord] = useState<AttachmentRecord>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let live = true;
    setRecord({ status: "loading" });
    readAttachment(id).then(
      (info) => {
        if (live) setRecord(info ? { status: "ready", info } : { status: "unavailable" });
      },
      () => {
        if (live) setRecord({ status: "failed" });
      },
    );
    return () => {
      live = false;
    };
  }, [id, attempt]);
  const retry = useCallback(() => setAttempt((count) => count + 1), []);
  return { record, retry };
}

/**
 * Rows and cells of a CSV text. Quotes may hold commas, doubled quotes and
 * line breaks. A row with no cells is skipped.
 */
export function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  for (let at = 0; at < text.length; at += 1) {
    const char = text[at]!;
    if (quoted) {
      if (char === '"' && text[at + 1] === '"') {
        cell += '"';
        at += 1;
      } else if (char === '"') {
        quoted = false;
      } else {
        cell += char;
      }
    } else if (char === '"') {
      quoted = true;
    } else if (char === ",") {
      row.push(cell);
      cell = "";
    } else if (char === "\n" || char === "\r") {
      if (char === "\r" && text[at + 1] === "\n") at += 1;
      row.push(cell);
      cell = "";
      if (row.some((value) => value !== "")) rows.push(row);
      row = [];
    } else {
      cell += char;
    }
  }
  row.push(cell);
  if (row.some((value) => value !== "")) rows.push(row);
  return rows;
}
